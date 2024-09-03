import streamlit as st
import requests
import time
import os
from io import BytesIO
from urllib.parse import urlparse, parse_qs
import yt_dlp
import subprocess
import re
from streamlit_float import *

# Float feature initialization
float_init()

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

API_URL = os.getenv("API_URL")
FFMPEG_PATH = os.getenv("FFMPEG_PATH")

# Base directory for temporary files
base_temp_dir = os.path.expanduser("~/transcription-whisper-temp")
os.makedirs(base_temp_dir, exist_ok=True)


def upload_file(file, lang, model, min_speakers, max_speakers):
    files = {'file': file}
    data = {
        'lang': lang,
        'model': model,
        'min_speakers': min_speakers,
        'max_speakers': max_speakers,
    }
    response = requests.post(f"{API_URL}/jobs", files=files, data=data)
    return response.json()


def check_status(task_id):
    response = requests.get(f"{API_URL}/jobs/{task_id}")
    return response.json()


def get_youtube_video_id(url):
    query = urlparse(url).query
    params = parse_qs(query)
    return params.get("v", [None])[0]


def download_youtube_video(youtube_url):
    video_id = get_youtube_video_id(youtube_url)
    if not video_id:
        raise ValueError("Invalid YouTube URL")

    temp_file_path = os.path.join(base_temp_dir, f"{video_id}.%(ext)s")
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': temp_file_path,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(youtube_url, download=True)
        downloaded_file_path = ydl.prepare_filename(info_dict)

    return downloaded_file_path


def convert_audio(input_path, output_path):
    try:
        input_path = os.path.abspath(input_path)
        output_path = os.path.abspath(output_path)

        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file does not exist: {input_path}")

        result = subprocess.run(
            [FFMPEG_PATH, '-y', '-i', input_path, output_path],
            capture_output=True, text=True, check=True
        )
        print(f"ffmpeg output: {result.stdout}")
        print(f"ffmpeg error (if any): {result.stderr}")
    except subprocess.CalledProcessError as e:
        print(f"ffmpeg failed: {e.stderr}")
        raise
    except Exception as e:
        print(f"An error occurred: {e}")
        raise


def parse_srt(srt_content):
    subtitles = []
    blocks = srt_content.strip().split("\n\n")
    for block in blocks:
        lines = block.split("\n")
        index = lines[0]
        time_range = lines[1].replace(",", ".")
        text = "\n".join(lines[2:])

        start, end = time_range.split(" --> ")
        speaker_match = re.match(r"^\[(.*)\]: (.*)$", text, re.MULTILINE)
        if speaker_match:
            speaker = speaker_match.group(1)
            text = speaker_match.group(2)
        else:
            speaker = ""

        subtitles.append({'index': index, 'start': start, 'end': end, 'text': text, 'speaker': speaker})
    return subtitles


def parse_vtt(vtt_content):
    subtitles = []
    blocks = vtt_content.strip().split("\n\n")
    for block in blocks:
        lines = block.split("\n")
        if len(lines) < 2:
            continue
        time_range = lines[0]
        text = "\n".join(lines[1:])

        start, end = time_range.split(" --> ")
        speaker_match = re.match(r"^\[(.*)\]: (.*)$", text, re.MULTILINE)
        if speaker_match:
            speaker = speaker_match.group(1)
            text = speaker_match.group(2)
        else:
            speaker = ""

        subtitles.append({'start': start, 'end': end, 'text': text, 'speaker': speaker})
    return subtitles


def display_edit_subtitles(subtitles):
    edited_subtitles = []

    st.markdown("---")

    for subtitle in subtitles:
        col1, col2, col3 = st.columns([1, 2, 6])
        with col1:
            st.markdown(f"**#{subtitle['index']}**")

        with col2:
            start_time = st.text_input("Start Time", subtitle['start'], key=f"start_{subtitle['index']}")
            end_time = st.text_input("End Time", subtitle['end'], key=f"end_{subtitle['index']}")

        with col3:
            speaker_name = st.text_input("Speaker", subtitle['speaker'], key=f"speaker_{subtitle['index']}",
                                         label_visibility="hidden")
            text = st.text_area("Text", subtitle['text'], key=f"text_{subtitle['index']}", label_visibility="hidden")

        st.markdown("---")

        edited_subtitles.append({'start': start_time, 'end': end_time, 'text': text, 'speaker': speaker_name})

    return edited_subtitles


def convert_subtitles_to_format(subtitles, format_type):
    if format_type == 'srt':
        result = []
        for i, subtitle in enumerate(subtitles, start=1):
            result.append(
                f"{i}\n{subtitle['start'].replace('.', ',')} --> {subtitle['end'].replace('.', ',')}\n[{subtitle['speaker']}]: {subtitle['text']}\n"
            )
        return "\n".join(result)
    elif format_type == 'vtt':
        result = ["WEBVTT\n"]
        for subtitle in subtitles:
            result.append(f"{subtitle['start']} --> {subtitle['end']}\n[{subtitle['speaker']}]: {subtitle['text']}\n")
        return "\n".join(result)
    else:
        return None


def display_speaker_inputs(subtitles):
    speaker_placeholders = set(
        subtitle['speaker'] for subtitle in subtitles if subtitle['speaker'].startswith('SPEAKER_'))
    speakers = {}

    st.write("Global Speaker Names")
    for placeholder in speaker_placeholders:
        speaker_name = st.text_input(f"Global name for {placeholder}", key=f"global_speaker_{placeholder}")
        speakers[placeholder] = speaker_name

    return speakers


def apply_global_speaker_names(subtitles, global_speaker_names):
    for subtitle in subtitles:
        if subtitle['speaker'] in global_speaker_names and global_speaker_names[subtitle['speaker']]:
            subtitle['speaker'] = global_speaker_names[subtitle['speaker']]
    return subtitles


def is_video_file(file_path):
    video_extensions = ['.mp4', '.avi', '.mov']
    return any(file_path.lower().endswith(ext) for ext in video_extensions)


def is_audio_file(file_path):
    audio_extensions = ['.mp3', '.wav', '.aac']
    return any(file_path.lower().endswith(ext) for ext in audio_extensions)


st.title("Transcription Service")
st.write("Upload a video or audio file or provide a YouTube link to get a transcription.")

input_type = st.radio("Choose input type", ["Upload File", "YouTube Link"])

uploaded_file = None
youtube_link = None

if input_type == "Upload File":
    uploaded_file = st.file_uploader("Choose a file", type=["mp4", "wav", "mp3"])
elif input_type == "YouTube Link":
    youtube_link = st.text_input("Enter YouTube video link")

lang = st.selectbox("Select Language", ["de", "en", "es", "fr", "pt"])
model = st.selectbox("Select Model", ["base", "large-v2", "large-v3"])
min_speakers = st.number_input("Minimum Number of Speakers", min_value=1, max_value=20, value=1)
max_speakers = st.number_input("Maximum Number of Speakers", min_value=1, max_value=20, value=2)

if "task_id" not in st.session_state:
    st.session_state.task_id = None
if "result" not in st.session_state:
    st.session_state.result = None
if "status" not in st.session_state:
    st.session_state.status = None
if "original_file_name" not in st.session_state:
    st.session_state.original_file_name = None
if "edited_txt_content" not in st.session_state:
    st.session_state.edited_txt_content = None
if "edited_subtitles" not in st.session_state:
    st.session_state.edited_subtitles = None
if "file_bytes" not in st.session_state:  # added to track byte contents
    st.session_state.file_bytes = None
if "unique_file_path" not in st.session_state:
    st.session_state.unique_file_path = None
if "original_file_type" not in st.session_state:
    st.session_state.original_file_type = None


def process_uploaded_file(uploaded_file):
    file_bytes = uploaded_file.read()
    if uploaded_file.type.startswith("audio"):
        st.session_state.original_file_type = 'audio'
    else:
        st.session_state.original_file_type = 'video'
    st.session_state.file_bytes = file_bytes  # Storing file_bytes
    return file_bytes, uploaded_file.name


def process_youtube_link(youtube_link):
    st.info("Downloading YouTube video...")
    downloaded_file_path = download_youtube_video(youtube_link)
    temp_output_path = f"{os.path.splitext(downloaded_file_path)[0]}.mp3"
    st.info("Converting video to mp3...")
    convert_audio(downloaded_file_path, temp_output_path)
    original_file_name = f"{get_youtube_video_id(youtube_link)}{os.path.splitext(downloaded_file_path)[1]}"
    st.session_state.original_file_type = 'video'  # Assume YouTube links are always videos for now
    return temp_output_path, original_file_name


if (uploaded_file or youtube_link) and st.button("Transcribe"):
    if uploaded_file:
        file_bytes, original_file_name = process_uploaded_file(uploaded_file)
        unique_file_path = os.path.join(base_temp_dir,
                                        f"{original_file_name}.mp3") if st.session_state.original_file_type == 'video' else file_bytes

        if st.session_state.original_file_type == 'video':
            with open(unique_file_path + '.tmp', 'wb') as temp_file:
                temp_file.write(file_bytes)
            convert_audio(unique_file_path + '.tmp', unique_file_path)
    elif youtube_link:
        unique_file_path, original_file_name = process_youtube_link(youtube_link)

    st.session_state.unique_file_path = unique_file_path

    st.info("Uploading file...")
    with open(unique_file_path, "rb") as file_to_transcribe:
        upload_response = upload_file(file_to_transcribe, lang, model, min_speakers, max_speakers)
    task_id = upload_response.get("task_id")
    if task_id:
        st.session_state.task_id = task_id
        st.session_state.original_file_name = original_file_name
        st.info(f"File uploaded. Tracking task with ID: {task_id}")

if st.session_state.task_id and st.session_state.status != "SUCCESS":
    st.info("Transcription is in progress. Please wait...")
    status_placeholder = st.empty()
    start_time = time.time()
    while True:
        status = check_status(st.session_state.task_id)
        elapsed_time = time.time() - start_time
        minutes, seconds = divmod(elapsed_time, 60)
        if status['status'] == "SUCCESS":
            st.session_state.status = "SUCCESS"
            st.session_state.result = status['result']
            break
        elif status['status'] == "FAILURE":
            st.session_state.status = "FAILURE"
            st.error(f"Transcription failed. Error: {status.get('error', 'Unknown error')}")
            break
        else:
            st.session_state.status = status['status']
            status_placeholder.info(
                f"Task Status: {status['status']}. Elapsed time: {int(minutes)} min {int(seconds)} sec. Checking again in 30 seconds..."
            )
            time.sleep(30)

if st.session_state.status == "SUCCESS" and st.session_state.result:
    result = st.session_state.result
    base_name = os.path.splitext(st.session_state.original_file_name)[0]

    txt_content = result['txt_content']
    vtt_content = result['vtt_content']
    json_content = result['json_content']
    srt_content = result['srt_content']

    file_type_to_edit = st.selectbox("Select file type to edit", ["TXT", "VTT", "SRT", "JSON"])

    if file_type_to_edit == "TXT":
        if "edited_txt_content" not in st.session_state or st.session_state.edited_txt_content is None:
            st.session_state.edited_txt_content = txt_content

        with st.expander("Edit Transcription"):
            st.session_state.edited_txt_content = st.text_area(
                "Transcription Text", value=st.session_state.edited_txt_content, height=400, max_chars=None
            )

        txt_file = BytesIO(st.session_state.edited_txt_content.encode('utf-8'))
        txt_file.name = f"{base_name}_edited.txt"

        st.download_button(
            label="Download Edited TXT File",
            data=txt_file,
            file_name=f"{base_name}_edited.txt",
            mime="text/plain"
        )

    elif file_type_to_edit in ["VTT", "SRT"]:
        if file_type_to_edit == "VTT":
            subtitles = parse_vtt(vtt_content)
        elif file_type_to_edit == "SRT":
            subtitles = parse_srt(srt_content)

        col1, col2 = st.columns([3, 2])

        with col1:
            with st.expander("Edit Subtitles:"):
                speakers = display_speaker_inputs(subtitles)
                edited_subtitles = display_edit_subtitles(subtitles)
                edited_subtitles = apply_global_speaker_names(edited_subtitles, speakers)

        col2.write("Overall Media")
        if st.session_state.original_file_type == 'video':
            col2.video(st.session_state.file_bytes)
        elif st.session_state.original_file_type == 'audio':
            col2.audio(st.session_state.file_bytes)
        col2.float()

        if st.button("Save and Download"):
            format_conversion_type = file_type_to_edit.lower()
            final_content = convert_subtitles_to_format(edited_subtitles, format_conversion_type)

            if final_content:
                subtitle_file = BytesIO(final_content.encode('utf-8'))
                subtitle_file.name = f"{base_name}_edited.{format_conversion_type}"

                st.download_button(
                    label=f"Download Edited {file_type_to_edit} File",
                    data=subtitle_file,
                    file_name=f"{base_name}_edited.{format_conversion_type}",
                    mime="text/plain" if format_conversion_type == "srt" else "text/vtt"
                )
    elif file_type_to_edit == "JSON":
        st.write("JSON Editing Not Implemented Yet.")
