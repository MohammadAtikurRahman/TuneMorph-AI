from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import librosa
import numpy as np
import pretty_midi
from basic_pitch import ICASSP_2022_MODEL_PATH
from basic_pitch.inference import predict
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool


# =========================================================
# PATH CONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
WORK_DIR = BASE_DIR / "work"

for directory in (
    UPLOADS_DIR,
    OUTPUTS_DIR,
    WORK_DIR,
):
    directory.mkdir(parents=True, exist_ok=True)


FLUIDSYNTH_EXE = (
    BASE_DIR
    / "tools"
    / "fluidsynth-v2.5.5-win10-x64-glib"
    / "bin"
    / "fluidsynth.exe"
)

PIANO_SF2 = BASE_DIR / "soundfonts" / "piano.sf2"

# piano.sf2 না থাকলে এই SoundFont ব্যবহার করবে।
if not PIANO_SF2.exists():
    fallback_soundfont = (
        BASE_DIR
        / "soundfonts"
        / "FluidR3_GM.sf2"
    )

    if fallback_soundfont.exists():
        PIANO_SF2 = fallback_soundfont


FFMPEG_EXE = "ffmpeg"
DEMUCS_MODEL = "htdemucs"
MAX_UPLOAD_MB = 100


ALLOWED_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".flac",
    ".ogg",
    ".m4a",
    ".aac",
    ".wma",
}


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(
    title="TuneMorph Piano API",
    version="1.0.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# ERROR CLASS
# =========================================================

class PipelineError(RuntimeError):
    pass


# =========================================================
# GENERAL HELPERS
# =========================================================

def find_executable(
    executable: str | Path,
    label: str,
) -> str:
    path = Path(executable)

    if path.exists() and path.is_file():
        return str(path.resolve())

    found = shutil.which(str(executable))

    if found:
        return found

    raise PipelineError(
        f"{label} পাওয়া যায়নি: {executable}"
    )


def require_file(
    path: Path,
    label: str,
) -> None:
    if not path.exists() or not path.is_file():
        raise PipelineError(
            f"{label} পাওয়া যায়নি: {path}"
        )


def run_command(
    command: list[str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=timeout,
    )

    if process.returncode != 0:
        error_message = (
            process.stderr.strip()
            or process.stdout.strip()
            or "External command failed."
        )

        raise PipelineError(error_message)

    return process


def safe_filename(
    filename: str | None,
) -> str:
    original_name = filename or "music.wav"

    cleaned_name = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        original_name,
    )

    cleaned_name = cleaned_name.strip("._")

    return cleaned_name or "music.wav"


# =========================================================
# AUDIO NORMALIZATION
# =========================================================

def normalize_audio(
    input_path: Path,
    output_path: Path,
) -> None:
    ffmpeg = find_executable(
        FFMPEG_EXE,
        "FFmpeg",
    )

    command = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]

    run_command(
        command,
        timeout=900,
    )


# =========================================================
# DEMUCS SEPARATION
# =========================================================

def get_demucs_device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"

    except Exception:
        pass

    return "cpu"


def separate_instrumental_music(
    input_audio: Path,
    separation_directory: Path,
) -> Path:
    """
    Full music থেকে vocals, drums, bass এবং other stem আলাদা করে।

    আমরা piano melody extraction-এর জন্য other.wav ব্যবহার করি।
    """

    device = get_demucs_device()

    command = [
        sys.executable,
        "-m",
        "demucs",
        "-n",
        DEMUCS_MODEL,
        "-d",
        device,
        "--segment",
        "7",
        "--overlap",
        "0.25",
        "-j",
        "1",
        "-o",
        str(separation_directory),
        str(input_audio),
    ]

    try:
        run_command(
            command,
            timeout=3600,
        )

    except PipelineError:
        # GPU error হলে CPU দিয়ে আবার চেষ্টা করবে।
        if device != "cuda":
            raise

        device_index = command.index("-d") + 1
        command[device_index] = "cpu"

        run_command(
            command,
            timeout=7200,
        )

    output_folder = (
        separation_directory
        / DEMUCS_MODEL
        / input_audio.stem
    )

    if not output_folder.exists():
        raise PipelineError(
            f"Demucs output folder পাওয়া যায়নি: "
            f"{output_folder}"
        )

    other_stem = output_folder / "other.wav"

    if not other_stem.exists():
        raise PipelineError(
            "Demucs other.wav stem তৈরি করেনি।"
        )

    return other_stem


# =========================================================
# TEMPO DETECTION
# =========================================================

def estimate_tempo(
    audio_path: Path,
) -> float:
    try:
        audio, sample_rate = librosa.load(
            str(audio_path),
            sr=22050,
            mono=True,
        )

        if audio.size == 0:
            return 120.0

        tempo, _ = librosa.beat.beat_track(
            y=audio,
            sr=sample_rate,
        )

        tempo_value = float(
            np.asarray(tempo).reshape(-1)[0]
        )

        if not np.isfinite(tempo_value):
            return 120.0

        if tempo_value < 45:
            return 120.0

        if tempo_value > 220:
            return 120.0

        return tempo_value

    except Exception:
        return 120.0


# =========================================================
# BASIC PITCH TRANSCRIPTION
# =========================================================

def transcribe_music(
    audio_path: Path,
) -> pretty_midi.PrettyMIDI:
    """
    Audio থেকে MIDI notes detect করে।
    """

    _, midi_data, _ = predict(
        audio_path,
        ICASSP_2022_MODEL_PATH,

        # বেশি false note আটকানোর জন্য।
        onset_threshold=0.58,
        frame_threshold=0.34,

        # খুব ছোট accidental note বাদ দেওয়ার জন্য।
        minimum_note_length=110.0,

        # C2 থেকে C7 range।
        minimum_frequency=float(
            librosa.note_to_hz("C2")
        ),
        maximum_frequency=float(
            librosa.note_to_hz("C7")
        ),

        multiple_pitch_bends=False,
        melodia_trick=True,
    )

    return midi_data


# =========================================================
# MIDI CLEANING
# =========================================================

def copy_note(
    note: pretty_midi.Note,
) -> pretty_midi.Note:
    return pretty_midi.Note(
        velocity=int(note.velocity),
        pitch=int(note.pitch),
        start=float(note.start),
        end=float(note.end),
    )


def collect_all_notes(
    midi_data: pretty_midi.PrettyMIDI,
) -> list[pretty_midi.Note]:
    notes: list[pretty_midi.Note] = []

    for instrument in midi_data.instruments:
        if instrument.is_drum:
            continue

        for note in instrument.notes:
            duration = note.end - note.start

            # অত্যন্ত ছোট note বাদ।
            if duration < 0.10:
                continue

            # Piano melody range।
            if note.pitch < 36:
                continue

            if note.pitch > 96:
                continue

            # খুব দুর্বল note বাদ।
            if note.velocity < 18:
                continue

            notes.append(
                copy_note(note)
            )

    return sorted(
        notes,
        key=lambda note: (
            note.start,
            -note.velocity,
            -note.pitch,
        ),
    )


def group_nearby_onsets(
    notes: list[pretty_midi.Note],
    threshold: float = 0.075,
) -> list[list[pretty_midi.Note]]:
    if not notes:
        return []

    groups: list[list[pretty_midi.Note]] = []

    current_group = [notes[0]]
    group_start = notes[0].start

    for note in notes[1:]:
        if note.start - group_start <= threshold:
            current_group.append(note)

        else:
            groups.append(current_group)

            current_group = [note]
            group_start = note.start

    groups.append(current_group)

    return groups


def note_quality_score(
    note: pretty_midi.Note,
) -> float:
    duration = min(
        note.end - note.start,
        1.6,
    )

    velocity_score = (
        note.velocity / 127.0
    ) * 1.55

    duration_score = duration * 0.35

    register_score = 0.0

    # সাধারণ melody register।
    if 55 <= note.pitch <= 88:
        register_score = 0.35

    elif note.pitch < 48:
        register_score = -0.30

    return (
        velocity_score
        + duration_score
        + register_score
    )


def melody_transition_score(
    previous_note: pretty_midi.Note,
    current_note: pretty_midi.Note,
) -> float:
    pitch_jump = abs(
        current_note.pitch
        - previous_note.pitch
    )

    if pitch_jump <= 2:
        pitch_score = 0.60

    elif pitch_jump <= 5:
        pitch_score = 0.42

    elif pitch_jump <= 7:
        pitch_score = 0.25

    elif pitch_jump <= 12:
        pitch_score = -0.12

    else:
        pitch_score = (
            -0.12
            - (pitch_jump - 12) * 0.075
        )

    gap = max(
        0.0,
        current_note.start
        - previous_note.end,
    )

    gap_penalty = (
        -max(0.0, gap - 1.5) * 0.10
    )

    return pitch_score + gap_penalty


def select_main_melody(
    notes: list[pretty_midi.Note],
) -> list[pretty_midi.Note]:
    """
    একই সময়ে একাধিক note থাকলে dynamic programming দিয়ে
    সবচেয়ে smooth এবং likely melody line নির্বাচন করে।
    """

    groups = group_nearby_onsets(notes)

    if not groups:
        return []

    candidates: list[
        list[pretty_midi.Note]
    ] = []

    for group in groups:
        strongest_notes = sorted(
            group,
            key=note_quality_score,
            reverse=True,
        )[:6]

        candidates.append(strongest_notes)

    scores: list[list[float]] = []
    backtrack: list[list[int]] = []

    first_scores = [
        note_quality_score(note)
        for note in candidates[0]
    ]

    scores.append(first_scores)
    backtrack.append(
        [-1] * len(candidates[0])
    )

    for group_index in range(
        1,
        len(candidates),
    ):
        current_scores: list[float] = []
        current_backtrack: list[int] = []

        for current_note in candidates[group_index]:
            best_score = -float("inf")
            best_previous_index = 0

            for (
                previous_index,
                previous_note,
            ) in enumerate(
                candidates[group_index - 1]
            ):
                candidate_score = (
                    scores[group_index - 1][
                        previous_index
                    ]
                    + note_quality_score(
                        current_note
                    )
                    + melody_transition_score(
                        previous_note,
                        current_note,
                    )
                )

                if candidate_score > best_score:
                    best_score = candidate_score
                    best_previous_index = (
                        previous_index
                    )

            current_scores.append(best_score)

            current_backtrack.append(
                best_previous_index
            )

        scores.append(current_scores)
        backtrack.append(current_backtrack)

    selected_index = int(
        np.argmax(scores[-1])
    )

    selected_reverse: list[
        pretty_midi.Note
    ] = []

    for group_index in range(
        len(candidates) - 1,
        -1,
        -1,
    ):
        selected_reverse.append(
            copy_note(
                candidates[group_index][
                    selected_index
                ]
            )
        )

        selected_index = backtrack[
            group_index
        ][selected_index]

        if selected_index < 0:
            break

    selected_notes = list(
        reversed(selected_reverse)
    )

    return selected_notes


def repair_octave_errors(
    notes: list[pretty_midi.Note],
) -> None:
    """
    Basic Pitch-এর probable octave jump ভুল ঠিক করে।
    """

    for index in range(
        1,
        len(notes),
    ):
        previous_pitch = (
            notes[index - 1].pitch
        )

        original_pitch = (
            notes[index].pitch
        )

        candidate_pitches = [
            original_pitch - 24,
            original_pitch - 12,
            original_pitch,
            original_pitch + 12,
            original_pitch + 24,
        ]

        candidate_pitches = [
            pitch
            for pitch in candidate_pitches
            if 36 <= pitch <= 96
        ]

        nearest_pitch = min(
            candidate_pitches,
            key=lambda pitch: abs(
                pitch - previous_pitch
            ),
        )

        original_jump = abs(
            original_pitch - previous_pitch
        )

        corrected_jump = abs(
            nearest_pitch - previous_pitch
        )

        # Only obvious octave mistakes।
        if (
            original_jump >= 13
            and corrected_jump <= 7
        ):
            notes[index].pitch = (
                nearest_pitch
            )


def merge_repeated_notes(
    notes: list[pretty_midi.Note],
) -> list[pretty_midi.Note]:
    if not notes:
        return []

    cleaned: list[
        pretty_midi.Note
    ] = []

    minimum_duration = 0.10

    for note in notes:
        if not cleaned:
            cleaned.append(note)
            continue

        previous_note = cleaned[-1]

        # একই note অল্প gap-এ আবার detect হলে merge।
        if (
            note.pitch
            == previous_note.pitch
            and note.start
            - previous_note.end
            <= 0.075
        ):
            previous_note.end = max(
                previous_note.end,
                note.end,
            )

            previous_note.velocity = max(
                previous_note.velocity,
                note.velocity,
            )

            continue

        # একসঙ্গে দুটি melody note বাজতে দেবে না।
        if note.start < previous_note.end:
            previous_note.end = max(
                previous_note.start
                + minimum_duration,
                note.start,
            )

        if (
            previous_note.end
            - previous_note.start
            < minimum_duration
        ):
            cleaned.pop()

        cleaned.append(note)

    return [
        note
        for note in cleaned
        if (
            note.end - note.start
            >= minimum_duration
        )
    ]


def quantize_notes(
    notes: list[pretty_midi.Note],
    tempo: float,
) -> None:
    """
    Timing একদম robotic না করে 30% quantization করে।
    """

    quantize_strength = 0.30

    beat_seconds = (
        60.0 / max(tempo, 1.0)
    )

    # 1/16 note grid।
    grid_seconds = beat_seconds / 4.0

    for note in notes:
        quantized_start = round(
            note.start / grid_seconds
        ) * grid_seconds

        quantized_end = round(
            note.end / grid_seconds
        ) * grid_seconds

        note.start = (
            note.start
            * (1.0 - quantize_strength)
            + quantized_start
            * quantize_strength
        )

        note.end = (
            note.end
            * (1.0 - quantize_strength)
            + quantized_end
            * quantize_strength
        )

        if note.end - note.start < 0.10:
            note.end = note.start + 0.10


def improve_piano_velocity(
    notes: list[pretty_midi.Note],
) -> None:
    """
    সব note একই volume না রেখে natural dynamics তৈরি করে।
    """

    for index, note in enumerate(notes):
        phrase_accent = (
            7 if index % 4 == 0 else 0
        )

        duration_accent = min(
            10,
            int(
                (note.end - note.start)
                * 7
            ),
        )

        original_velocity = int(
            (note.velocity - 64) * 0.18
        )

        final_velocity = (
            76
            + phrase_accent
            + duration_accent
            + original_velocity
        )

        note.velocity = int(
            np.clip(
                final_velocity,
                48,
                108,
            )
        )


def add_sustain_pedal(
    piano: pretty_midi.Instrument,
    notes: list[pretty_midi.Note],
    tempo: float,
) -> None:
    if not notes:
        return

    beat_seconds = (
        60.0 / max(tempo, 1.0)
    )

    pedal_duration = (
        beat_seconds * 2.0
    )

    start_time = max(
        0.0,
        notes[0].start,
    )

    ending_time = notes[-1].end

    while start_time < ending_time:
        release_time = min(
            start_time
            + pedal_duration
            - 0.04,
            ending_time,
        )

        piano.control_changes.append(
            pretty_midi.ControlChange(
                number=64,
                value=72,
                time=start_time,
            )
        )

        piano.control_changes.append(
            pretty_midi.ControlChange(
                number=64,
                value=0,
                time=release_time,
            )
        )

        start_time += pedal_duration


def create_clean_piano_midi(
    predicted_midi: pretty_midi.PrettyMIDI,
    source_audio: Path,
    output_midi: Path,
) -> tuple[int, float]:
    tempo = estimate_tempo(
        source_audio
    )

    notes = collect_all_notes(
        predicted_midi
    )

    notes = select_main_melody(
        notes
    )

    repair_octave_errors(
        notes
    )

    notes = merge_repeated_notes(
        notes
    )

    quantize_notes(
        notes,
        tempo,
    )

    improve_piano_velocity(
        notes
    )

    if not notes:
        raise PipelineError(
            "কোনো usable melody note পাওয়া যায়নি। "
            "আরও পরিষ্কার instrumental music ব্যবহার করো।"
        )

    output = pretty_midi.PrettyMIDI(
        initial_tempo=tempo
    )

    piano_program = (
        pretty_midi
        .instrument_name_to_program(
            "Acoustic Grand Piano"
        )
    )

    piano = pretty_midi.Instrument(
        program=piano_program,
        is_drum=False,
        name="TuneMorph Piano",
    )

    piano.notes.extend(notes)

    add_sustain_pedal(
        piano,
        notes,
        tempo,
    )

    output.instruments.append(piano)

    output.write(
        str(output_midi)
    )

    return len(notes), tempo


# =========================================================
# PIANO RENDERING
# =========================================================

def render_midi_to_wav(
    midi_path: Path,
    wav_path: Path,
) -> None:
    fluidsynth = find_executable(
        FLUIDSYNTH_EXE,
        "FluidSynth",
    )

    require_file(
        PIANO_SF2,
        "Piano SoundFont",
    )

    command = [
        fluidsynth,
        "-ni",

        # Output volume।
        "-g",
        "0.78",

        # Sample rate।
        "-r",
        "44100",

        # Output WAV।
        "-F",
        str(wav_path),

        # SoundFont।
        str(PIANO_SF2),

        # MIDI file।
        str(midi_path),
    ]

    run_command(
        command,
        timeout=1800,
    )


# =========================================================
# AUDIO POST-PROCESSING
# =========================================================

def create_final_audio(
    raw_wav: Path,
    final_wav: Path,
    final_mp3: Path,
) -> None:
    ffmpeg = find_executable(
        FFMPEG_EXE,
        "FFmpeg",
    )

    audio_filter = (
        "highpass=f=28,"
        "lowpass=f=17500,"
        "loudnorm=I=-16:TP=-1.5:LRA=11"
    )

    # Final WAV।
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            str(raw_wav),
            "-af",
            audio_filter,
            "-c:a",
            "pcm_s16le",
            str(final_wav),
        ],
        timeout=900,
    )

    # Final MP3।
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            str(final_wav),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "256k",
            str(final_mp3),
        ],
        timeout=900,
    )


# =========================================================
# COMPLETE PIPELINE
# =========================================================

def convert_music_to_piano(
    uploaded_file: Path,
    job_work_directory: Path,
) -> dict:
    normalized_audio = (
        job_work_directory
        / "normalized.wav"
    )

    normalize_audio(
        uploaded_file,
        normalized_audio,
    )

    instrumental_stem = (
        separate_instrumental_music(
            normalized_audio,
            job_work_directory
            / "separated",
        )
    )

    predicted_midi = transcribe_music(
        instrumental_stem
    )

    piano_midi = (
        job_work_directory
        / "piano.mid"
    )

    note_count, tempo = (
        create_clean_piano_midi(
            predicted_midi,
            instrumental_stem,
            piano_midi,
        )
    )

    raw_wav = (
        job_work_directory
        / "piano_raw.wav"
    )

    final_wav = (
        job_work_directory
        / "piano.wav"
    )

    final_mp3 = (
        job_work_directory
        / "piano.mp3"
    )

    render_midi_to_wav(
        piano_midi,
        raw_wav,
    )

    create_final_audio(
        raw_wav,
        final_wav,
        final_mp3,
    )

    return {
        "midi": piano_midi,
        "wav": final_wav,
        "mp3": final_mp3,
        "note_count": note_count,
        "tempo": tempo,
    }


# =========================================================
# ROUTES
# =========================================================

@app.get("/")
def root() -> dict:
    return {
        "message": (
            "TuneMorph Piano API is running."
        ),
        "docs": "/docs",
    }


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "fluidsynth_found": (
            FLUIDSYNTH_EXE.exists()
        ),
        "piano_soundfont_found": (
            PIANO_SF2.exists()
        ),
        "ffmpeg_found": (
            shutil.which(
                FFMPEG_EXE
            )
            is not None
        ),
        "demucs_model": DEMUCS_MODEL,
    }


@app.post("/api/convert")
async def convert(
    file: UploadFile = File(...),
) -> dict:
    filename = safe_filename(
        file.filename
    )

    extension = (
        Path(filename)
        .suffix
        .lower()
    )

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported audio format. "
                "Allowed: "
                + ", ".join(
                    sorted(
                        ALLOWED_EXTENSIONS
                    )
                )
            ),
        )

    job_id = uuid.uuid4().hex

    upload_directory = (
        UPLOADS_DIR / job_id
    )

    work_directory = (
        WORK_DIR / job_id
    )

    output_directory = (
        OUTPUTS_DIR / job_id
    )

    upload_directory.mkdir(
        parents=True,
        exist_ok=False,
    )

    work_directory.mkdir(
        parents=True,
        exist_ok=False,
    )

    output_directory.mkdir(
        parents=True,
        exist_ok=False,
    )

    uploaded_path = (
        upload_directory / filename
    )

    try:
        maximum_bytes = (
            MAX_UPLOAD_MB
            * 1024
            * 1024
        )

        total_bytes = 0

        with uploaded_path.open(
            "wb"
        ) as output_file:
            while True:
                chunk = await file.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                total_bytes += len(chunk)

                if total_bytes > maximum_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"File must be smaller "
                            f"than {MAX_UPLOAD_MB} MB."
                        ),
                    )

                output_file.write(chunk)

        result = await run_in_threadpool(
            convert_music_to_piano,
            uploaded_path,
            work_directory,
        )

        final_midi = (
            output_directory
            / "piano.mid"
        )

        final_wav = (
            output_directory
            / "piano.wav"
        )

        final_mp3 = (
            output_directory
            / "piano.mp3"
        )

        shutil.copy2(
            result["midi"],
            final_midi,
        )

        shutil.copy2(
            result["wav"],
            final_wav,
        )

        shutil.copy2(
            result["mp3"],
            final_mp3,
        )

        return {
            "ok": True,
            "job_id": job_id,
            "note_count": (
                result["note_count"]
            ),
            "estimated_tempo": round(
                float(result["tempo"]),
                2,
            ),
            "audio_url": (
                f"/api/output/"
                f"{job_id}/piano.mp3"
            ),
            "wav_url": (
                f"/api/output/"
                f"{job_id}/piano.wav"
            ),
            "midi_url": (
                f"/api/output/"
                f"{job_id}/piano.mid"
            ),
        }

    except HTTPException:
        shutil.rmtree(
            output_directory,
            ignore_errors=True,
        )

        raise

    except PipelineError as error:
        shutil.rmtree(
            output_directory,
            ignore_errors=True,
        )

        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error

    except Exception as error:
        shutil.rmtree(
            output_directory,
            ignore_errors=True,
        )

        raise HTTPException(
            status_code=500,
            detail=(
                f"Conversion failed: {error}"
            ),
        ) from error

    finally:
        await file.close()

        shutil.rmtree(
            upload_directory,
            ignore_errors=True,
        )

        shutil.rmtree(
            work_directory,
            ignore_errors=True,
        )


@app.get(
    "/api/output/{job_id}/{filename}"
)
def get_output_file(
    job_id: str,
    filename: str,
) -> FileResponse:
    if not re.fullmatch(
        r"[a-f0-9]{32}",
        job_id,
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid job ID.",
        )

    allowed_files = {
        "piano.mp3",
        "piano.wav",
        "piano.mid",
    }

    if filename not in allowed_files:
        raise HTTPException(
            status_code=404,
            detail="File not found.",
        )

    output_path = (
        OUTPUTS_DIR
        / job_id
        / filename
    ).resolve()

    expected_parent = (
        OUTPUTS_DIR
        / job_id
    ).resolve()

    if output_path.parent != expected_parent:
        raise HTTPException(
            status_code=404,
            detail="File not found.",
        )

    if not output_path.exists():
        raise HTTPException(
            status_code=404,
            detail="File not found.",
        )

    media_types = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".mid": "audio/midi",
    }

    return FileResponse(
        output_path,
        media_type=media_types[
            output_path.suffix.lower()
        ],
        filename=filename,
    )