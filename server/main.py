from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np
import pretty_midi
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from scipy.ndimage import median_filter
from scipy.signal import butter, sosfiltfilt
from starlette.concurrency import run_in_threadpool

try:
    import torch
    import torchcrepe

    TORCHCREPE_IMPORT_ERROR: Exception | None = None

except Exception as import_error:
    torch = None
    torchcrepe = None
    TORCHCREPE_IMPORT_ERROR = import_error


# =========================================================
# CONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

UPLOADS_DIR = BASE_DIR / "uploads"
WORK_DIR = BASE_DIR / "work"
OUTPUTS_DIR = BASE_DIR / "outputs"
SOUNDFONTS_DIR = BASE_DIR / "soundfonts"

for folder in (
    UPLOADS_DIR,
    WORK_DIR,
    OUTPUTS_DIR,
    SOUNDFONTS_DIR,
):
    folder.mkdir(
        parents=True,
        exist_ok=True,
    )


FLUIDSYNTH_EXE = Path(
    os.getenv(
        "FLUIDSYNTH_EXE",
        str(
            BASE_DIR
            / "tools"
            / "fluidsynth-v2.5.5-win10-x64-glib"
            / "bin"
            / "fluidsynth.exe"
        ),
    )
)

FFMPEG_EXE = os.getenv(
    "FFMPEG_EXE",
    "ffmpeg",
)

# Better separation, but slower.
DEMUCS_MODEL = os.getenv(
    "DEMUCS_MODEL",
    "htdemucs_ft",
)

# তোমার installed Demucs integer segment গ্রহণ করে।
DEMUCS_SEGMENT = os.getenv(
    "DEMUCS_SEGMENT",
    "7",
)

# 2 prediction average করবে।
DEMUCS_SHIFTS = os.getenv(
    "DEMUCS_SHIFTS",
    "2",
)

MAX_UPLOAD_MB = int(
    os.getenv(
        "MAX_UPLOAD_MB",
        "150",
    )
)

OUTPUT_RETENTION_HOURS = int(
    os.getenv(
        "OUTPUT_RETENTION_HOURS",
        "24",
    )
)


# Pitch tracker settings
PITCH_SAMPLE_RATE = 16000

# 160 samples = 10 milliseconds
PITCH_HOP_LENGTH = 160

PITCH_MIN_FREQUENCY = 65.0
PITCH_MAX_FREQUENCY = 1400.0

PITCH_CONFIDENCE_THRESHOLD = 0.36

MINIMUM_NOTE_SECONDS = 0.085
MAXIMUM_SHORT_GAP_SECONDS = 0.060

# Timing correction strength.
# 0 = no correction
# 1 = fully robotic quantization
QUANTIZE_STRENGTH = 0.24

GRID_SUBDIVISIONS_PER_BEAT = 4


ALLOWED_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".flac",
    ".ogg",
    ".m4a",
    ".aac",
    ".wma",
}


# একসঙ্গে একটির বেশি heavy conversion চলবে না।
PIPELINE_LOCK = threading.Lock()


# =========================================================
# DATA CLASSES
# =========================================================

@dataclass
class NoteEvent:
    start: float
    end: float
    pitch: int
    confidence: float
    energy: float
    velocity: int = 80

    @property
    def duration(self) -> float:
        return self.end - self.start


class PipelineError(RuntimeError):
    pass


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(
    title="TuneMorph Piano API",
    version="2.0.0",
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
# GENERAL HELPERS
# =========================================================

def find_soundfont() -> Path:
    preferred_files = [
        SOUNDFONTS_DIR / "piano.sf2",
        SOUNDFONTS_DIR / "Piano.sf2",
        SOUNDFONTS_DIR / "FluidR3_GM.sf2",
    ]

    for path in preferred_files:
        if path.exists() and path.is_file():
            return path

    available_files = sorted(
        SOUNDFONTS_DIR.glob("*.sf2")
    )

    if available_files:
        return available_files[0]

    return SOUNDFONTS_DIR / "piano.sf2"


def find_executable(
    executable: str | Path,
    label: str,
) -> str:
    path = Path(executable)

    if path.exists() and path.is_file():
        return str(path.resolve())

    found = shutil.which(
        str(executable)
    )

    if found:
        return found

    raise PipelineError(
        f"{label} পাওয়া যায়নি: {executable}"
    )


def require_dependencies() -> None:
    if importlib.util.find_spec("demucs") is None:
        raise PipelineError(
            "Demucs install করা নেই। চালাও: "
            r".\venv311\Scripts\python.exe "
            r"-m pip install demucs"
        )

    if torch is None or torchcrepe is None:
        reason = (
            f" ({TORCHCREPE_IMPORT_ERROR})"
            if TORCHCREPE_IMPORT_ERROR
            else ""
        )

        raise PipelineError(
            "TorchCREPE load হচ্ছে না"
            f"{reason}. চালাও: "
            r".\venv311\Scripts\python.exe "
            r"-m pip install torchcrepe"
        )


def run_command(
    command: list[str],
    timeout: int,
) -> None:
    creation_flags = 0

    if os.name == "nt":
        creation_flags = getattr(
            subprocess,
            "CREATE_NO_WINDOW",
            0,
        )

    process = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=timeout,
        creationflags=creation_flags,
    )

    if process.returncode != 0:
        error_message = (
            process.stderr.strip()
            or process.stdout.strip()
            or "External command failed."
        )

        raise PipelineError(
            error_message
        )


def safe_filename(
    filename: str | None,
) -> str:
    filename = filename or "music.wav"

    filename = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        filename,
    )

    filename = filename.strip("._")

    return filename or "music.wav"


def cleanup_old_outputs() -> None:
    cutoff_time = (
        time.time()
        - OUTPUT_RETENTION_HOURS * 3600
    )

    for folder in OUTPUTS_DIR.iterdir():
        try:
            if (
                folder.is_dir()
                and folder.stat().st_mtime
                < cutoff_time
            ):
                shutil.rmtree(
                    folder,
                    ignore_errors=True,
                )

        except OSError:
            continue


# =========================================================
# AUDIO NORMALIZATION
# =========================================================

def normalize_audio(
    source_path: Path,
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
        str(source_path),
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
        timeout=1200,
    )


# =========================================================
# DEMUCS SEPARATION
# =========================================================

def get_processing_device() -> str:
    require_dependencies()

    if torch.cuda.is_available():
        return "cuda"

    return "cpu"


def separate_music(
    normalized_audio: Path,
    separation_directory: Path,
) -> Path:
    require_dependencies()

    device = get_processing_device()

    command = [
        sys.executable,
        "-m",
        "demucs",

        "-n",
        DEMUCS_MODEL,

        "-d",
        device,

        "--segment",
        DEMUCS_SEGMENT,

        "--shifts",
        DEMUCS_SHIFTS,

        "--overlap",
        "0.25",

        "--float32",

        "-j",
        "1",

        "-o",
        str(separation_directory),

        str(normalized_audio),
    ]

    try:
        run_command(
            command,
            timeout=7200,
        )

    except PipelineError:
        # GPU error হলে CPU-তে retry।
        if device != "cuda":
            raise

        device_position = (
            command.index("-d") + 1
        )

        command[device_position] = "cpu"

        run_command(
            command,
            timeout=14400,
        )

    output_folder = (
        separation_directory
        / DEMUCS_MODEL
        / normalized_audio.stem
    )

    other_stem = (
        output_folder
        / "other.wav"
    )

    if not other_stem.exists():
        raise PipelineError(
            "Demucs other.wav তৈরি করেনি: "
            f"{other_stem}"
        )

    return other_stem


# =========================================================
# MELODY AUDIO PREPARATION
# =========================================================

def apply_bandpass_filter(
    audio: np.ndarray,
    sample_rate: int,
) -> np.ndarray:
    low_frequency = 55.0

    high_frequency = min(
        5000.0,
        sample_rate * 0.45,
    )

    filter_sections = butter(
        4,
        [
            low_frequency,
            high_frequency,
        ],
        btype="bandpass",
        fs=sample_rate,
        output="sos",
    )

    try:
        filtered = sosfiltfilt(
            filter_sections,
            audio,
        )

        return filtered.astype(
            np.float32
        )

    except ValueError:
        return audio.astype(
            np.float32
        )


def prepare_pitch_audio(
    audio_path: Path,
) -> tuple[
    np.ndarray,
    np.ndarray,
    int,
]:
    original_audio, sample_rate = (
        librosa.load(
            str(audio_path),
            sr=PITCH_SAMPLE_RATE,
            mono=True,
        )
    )

    if (
        original_audio.size
        < sample_rate // 2
    ):
        raise PipelineError(
            "Audio file খুব ছোট বা খালি।"
        )

    original_audio = np.nan_to_num(
        original_audio,
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    ).astype(np.float32)

    original_peak = float(
        np.max(
            np.abs(original_audio)
        )
    )

    if original_peak < 1e-6:
        raise PipelineError(
            "Audio-তে ব্যবহারযোগ্য "
            "signal পাওয়া যায়নি।"
        )

    original_audio = (
        original_audio
        / original_peak
    )

    # Percussion এবং noise কমিয়ে
    # sustained melody বাড়ায়।
    harmonic_audio = (
        librosa.effects.harmonic(
            original_audio,
            margin=4.0,
        )
        .astype(np.float32)
    )

    # শুধু harmonic রাখলে attack হারাতে পারে,
    # তাই original-এর অল্প অংশ রাখা হচ্ছে।
    pitch_audio = (
        harmonic_audio * 0.88
        + original_audio * 0.12
    )

    pitch_audio = apply_bandpass_filter(
        pitch_audio,
        sample_rate,
    )

    pitch_peak = float(
        np.max(
            np.abs(pitch_audio)
        )
    )

    if pitch_peak > 1e-6:
        pitch_audio = (
            pitch_audio
            / pitch_peak
            * 0.95
        )

    return (
        original_audio.astype(
            np.float32
        ),
        pitch_audio.astype(
            np.float32
        ),
        sample_rate,
    )


# =========================================================
# TORCHCREPE PITCH TRACKING
# =========================================================

def predict_pitch_on_device(
    audio_tensor,
    sample_rate: int,
    device: str,
):
    device_audio = audio_tensor.to(
        device
    )

    batch_size = (
        1024
        if device.startswith("cuda")
        else 256
    )

    pitch, periodicity = (
        torchcrepe.predict(
            device_audio,
            sample_rate,
            PITCH_HOP_LENGTH,
            PITCH_MIN_FREQUENCY,
            PITCH_MAX_FREQUENCY,
            "full",
            decoder=(
                torchcrepe
                .decode
                .viterbi
            ),
            return_periodicity=True,
            batch_size=batch_size,
            device=device,
            pad=True,
        )
    )

    # Silent frame-এ false pitch কমায়।
    periodicity = (
        torchcrepe.threshold.Silence(
            -55.0
        )(
            periodicity,
            device_audio,
            sample_rate,
            PITCH_HOP_LENGTH,
        )
    )

    # Confidence signal smooth করা।
    periodicity = (
        torchcrepe.filter.median(
            periodicity,
            5,
        )
    )

    # Low-confidence pitch বাদ।
    pitch = (
        torchcrepe.threshold.At(
            PITCH_CONFIDENCE_THRESHOLD
        )(
            pitch,
            periodicity,
        )
    )

    # Pitch quantization noise কমানো।
    pitch = (
        torchcrepe.filter.mean(
            pitch,
            3,
        )
    )

    return pitch, periodicity


def track_pitch(
    audio: np.ndarray,
    sample_rate: int,
) -> tuple[
    np.ndarray,
    np.ndarray,
]:
    require_dependencies()

    audio_tensor = (
        torch.from_numpy(audio)
        .float()
        .unsqueeze(0)
    )

    device = (
        "cuda"
        if torch.cuda.is_available()
        else "cpu"
    )

    try:
        with torch.inference_mode():
            pitch, periodicity = (
                predict_pitch_on_device(
                    audio_tensor,
                    sample_rate,
                    device,
                )
            )

    except RuntimeError as error:
        if device != "cuda":
            raise PipelineError(
                "TorchCREPE pitch detection "
                f"failed: {error}"
            ) from error

        torch.cuda.empty_cache()

        with torch.inference_mode():
            pitch, periodicity = (
                predict_pitch_on_device(
                    audio_tensor,
                    sample_rate,
                    "cpu",
                )
            )

    pitch_array = (
        pitch
        .detach()
        .cpu()
        .numpy()
        .reshape(-1)
        .astype(np.float64)
    )

    periodicity_array = (
        periodicity
        .detach()
        .cpu()
        .numpy()
        .reshape(-1)
        .astype(np.float64)
    )

    return (
        pitch_array,
        periodicity_array,
    )


# =========================================================
# AUDIO FEATURES
# =========================================================

def fit_array_length(
    values: np.ndarray,
    target_length: int,
) -> np.ndarray:
    values = np.asarray(
        values
    ).reshape(-1)

    if len(values) >= target_length:
        return values[
            :target_length
        ]

    return np.pad(
        values,
        (
            0,
            target_length - len(values),
        ),
        mode="constant",
    )


def calculate_frame_features(
    original_audio: np.ndarray,
    sample_rate: int,
    frame_count: int,
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    float,
]:
    rms = librosa.feature.rms(
        y=original_audio,
        frame_length=1024,
        hop_length=PITCH_HOP_LENGTH,
        center=True,
    )[0]

    onset_envelope = (
        librosa.onset.onset_strength(
            y=original_audio,
            sr=sample_rate,
            hop_length=PITCH_HOP_LENGTH,
        )
    )

    tempo, beat_frames = (
        librosa.beat.beat_track(
            y=original_audio,
            sr=sample_rate,
            hop_length=PITCH_HOP_LENGTH,
            units="frames",
        )
    )

    tempo_value = float(
        np.asarray(tempo)
        .reshape(-1)[0]
    )

    if (
        not np.isfinite(tempo_value)
        or tempo_value < 40
        or tempo_value > 240
    ):
        tempo_value = 120.0

    return (
        fit_array_length(
            rms,
            frame_count,
        ),
        fit_array_length(
            onset_envelope,
            frame_count,
        ),
        np.asarray(
            beat_frames,
            dtype=int,
        ),
        tempo_value,
    )


# =========================================================
# PITCH TO MIDI LABELS
# =========================================================

def frequency_to_midi(
    pitch_hz: np.ndarray,
) -> np.ndarray:
    midi_pitch = np.full(
        pitch_hz.shape,
        np.nan,
        dtype=np.float64,
    )

    valid = (
        np.isfinite(pitch_hz)
        & (pitch_hz > 0)
    )

    midi_pitch[valid] = (
        69.0
        + 12.0
        * np.log2(
            pitch_hz[valid] / 440.0
        )
    )

    return midi_pitch


def smooth_pitch_labels(
    midi_pitch: np.ndarray,
    periodicity: np.ndarray,
) -> np.ndarray:
    voiced = (
        np.isfinite(midi_pitch)
        & (
            periodicity
            >= PITCH_CONFIDENCE_THRESHOLD
        )
    )

    labels = np.full(
        len(midi_pitch),
        -1,
        dtype=np.int16,
    )

    if not np.any(voiced):
        return labels

    raw_labels = np.rint(
        np.nan_to_num(
            midi_pitch,
            nan=0.0,
        )
    ).astype(np.int16)

    voiced_indices = np.flatnonzero(
        voiced
    )

    island_start = 0

    while island_start < len(
        voiced_indices
    ):
        island_end = (
            island_start + 1
        )

        while (
            island_end
            < len(voiced_indices)
            and voiced_indices[island_end]
            == voiced_indices[
                island_end - 1
            ] + 1
        ):
            island_end += 1

        island_indices = (
            voiced_indices[
                island_start:island_end
            ]
        )

        island_labels = raw_labels[
            island_indices
        ]

        if len(island_labels) >= 5:
            island_labels = (
                median_filter(
                    island_labels,
                    size=5,
                    mode="nearest",
                )
            )

        labels[
            island_indices
        ] = island_labels

        island_start = island_end

    maximum_gap_frames = max(
        1,
        round(
            MAXIMUM_SHORT_GAP_SECONDS
            * PITCH_SAMPLE_RATE
            / PITCH_HOP_LENGTH
        ),
    )

    frame_index = 0

    while frame_index < len(labels):
        if labels[frame_index] >= 0:
            frame_index += 1
            continue

        gap_start = frame_index

        while (
            frame_index < len(labels)
            and labels[frame_index] < 0
        ):
            frame_index += 1

        gap_end = frame_index

        left_pitch = (
            labels[gap_start - 1]
            if gap_start > 0
            else -1
        )

        right_pitch = (
            labels[gap_end]
            if gap_end < len(labels)
            else -1
        )

        if (
            gap_end - gap_start
            <= maximum_gap_frames
            and left_pitch >= 0
            and right_pitch >= 0
            and abs(
                int(left_pitch)
                - int(right_pitch)
            ) <= 1
        ):
            labels[
                gap_start:gap_end
            ] = left_pitch

    return labels


# =========================================================
# ONSET DETECTION
# =========================================================

def get_strong_onset_frames(
    onset_envelope: np.ndarray,
) -> set[int]:
    positive_values = (
        onset_envelope[
            onset_envelope > 0
        ]
    )

    if positive_values.size == 0:
        return set()

    strength_threshold = float(
        np.percentile(
            positive_values,
            72,
        )
    )

    peak_frames = (
        librosa.util.peak_pick(
            onset_envelope,
            pre_max=3,
            post_max=3,
            pre_avg=6,
            post_avg=6,
            delta=max(
                0.05,
                strength_threshold * 0.10,
            ),
            wait=5,
        )
    )

    return {
        int(frame)
        for frame in peak_frames
        if (
            onset_envelope[frame]
            >= strength_threshold
        )
    }


def split_pitch_run_at_onsets(
    start_frame: int,
    end_frame: int,
    onset_frames: set[int],
    minimum_frames: int,
) -> list[tuple[int, int]]:
    boundaries = [start_frame]

    for onset_frame in sorted(
        onset_frames
    ):
        if (
            start_frame
            < onset_frame
            < end_frame
            and onset_frame
            - boundaries[-1]
            >= minimum_frames
            and end_frame
            - onset_frame
            >= minimum_frames
        ):
            boundaries.append(
                onset_frame
            )

    boundaries.append(
        end_frame
    )

    return [
        (
            boundaries[index],
            boundaries[index + 1],
        )
        for index in range(
            len(boundaries) - 1
        )
    ]


# =========================================================
# LABELS TO NOTES
# =========================================================

def labels_to_note_events(
    labels: np.ndarray,
    midi_pitch: np.ndarray,
    periodicity: np.ndarray,
    rms: np.ndarray,
    onset_envelope: np.ndarray,
) -> list[NoteEvent]:
    hop_seconds = (
        PITCH_HOP_LENGTH
        / PITCH_SAMPLE_RATE
    )

    minimum_frames = max(
        3,
        round(
            MINIMUM_NOTE_SECONDS
            / hop_seconds
        ),
    )

    onset_frames = (
        get_strong_onset_frames(
            onset_envelope
        )
    )

    events: list[NoteEvent] = []

    frame_index = 0

    while frame_index < len(labels):
        pitch_label = int(
            labels[frame_index]
        )

        if pitch_label < 0:
            frame_index += 1
            continue

        run_start = frame_index

        frame_index += 1

        while (
            frame_index < len(labels)
            and int(labels[frame_index])
            == pitch_label
        ):
            frame_index += 1

        run_end = frame_index

        if (
            run_end - run_start
            < minimum_frames
        ):
            continue

        parts = split_pitch_run_at_onsets(
            run_start,
            run_end,
            onset_frames,
            minimum_frames,
        )

        for part_start, part_end in parts:
            if (
                part_end - part_start
                < minimum_frames
            ):
                continue

            pitch_values = midi_pitch[
                part_start:part_end
            ]

            pitch_values = (
                pitch_values[
                    np.isfinite(
                        pitch_values
                    )
                ]
            )

            if pitch_values.size:
                final_pitch = int(
                    np.rint(
                        np.median(
                            pitch_values
                        )
                    )
                )
            else:
                final_pitch = pitch_label

            final_pitch = int(
                np.clip(
                    final_pitch,
                    36,
                    96,
                )
            )

            confidence = float(
                np.nanmedian(
                    periodicity[
                        part_start:part_end
                    ]
                )
            )

            energy = float(
                np.nanmedian(
                    rms[
                        part_start:part_end
                    ]
                )
            )

            events.append(
                NoteEvent(
                    start=(
                        part_start
                        * hop_seconds
                    ),
                    end=(
                        part_end
                        * hop_seconds
                    ),
                    pitch=final_pitch,
                    confidence=confidence,
                    energy=energy,
                )
            )

    return events


# =========================================================
# NOTE CLEANUP
# =========================================================

def clean_note_events(
    events: list[NoteEvent],
) -> list[NoteEvent]:
    cleaned: list[NoteEvent] = []

    for event in events:
        if (
            event.duration
            < MINIMUM_NOTE_SECONDS
        ):
            continue

        if not cleaned:
            cleaned.append(event)
            continue

        previous = cleaned[-1]

        gap = (
            event.start
            - previous.end
        )

        # একই note-এর ছোট gap merge।
        if (
            event.pitch
            == previous.pitch
            and gap
            <= MAXIMUM_SHORT_GAP_SECONDS
        ):
            previous.end = max(
                previous.end,
                event.end,
            )

            previous.confidence = max(
                previous.confidence,
                event.confidence,
            )

            previous.energy = max(
                previous.energy,
                event.energy,
            )

            continue

        # Monophonic melody:
        # একসঙ্গে দুই note বাজবে না।
        if event.start < previous.end:
            previous.end = (
                event.start - 0.005
            )

            if (
                previous.duration
                < MINIMUM_NOTE_SECONDS
            ):
                cleaned.pop()

        cleaned.append(event)

    # Short isolated octave glitch repair।
    event_index = 1

    while (
        event_index
        < len(cleaned) - 1
    ):
        previous = cleaned[
            event_index - 1
        ]

        current = cleaned[
            event_index
        ]

        following = cleaned[
            event_index + 1
        ]

        probable_glitch = (
            current.duration <= 0.22
            and abs(
                current.pitch
                - previous.pitch
            ) >= 10
            and abs(
                current.pitch
                - following.pitch
            ) >= 10
            and abs(
                previous.pitch
                - following.pitch
            ) <= 2
        )

        if probable_glitch:
            candidates = [
                current.pitch - 24,
                current.pitch - 12,
                current.pitch,
                current.pitch + 12,
                current.pitch + 24,
            ]

            candidates = [
                pitch
                for pitch in candidates
                if 36 <= pitch <= 96
            ]

            target_pitch = (
                previous.pitch
                + following.pitch
            ) / 2.0

            corrected_pitch = min(
                candidates,
                key=lambda pitch: abs(
                    pitch - target_pitch
                ),
            )

            if (
                abs(
                    corrected_pitch
                    - target_pitch
                ) <= 4
            ):
                current.pitch = int(
                    corrected_pitch
                )

            elif current.confidence < 0.48:
                previous.end = current.end

                cleaned.pop(
                    event_index
                )

                continue

        event_index += 1

    return cleaned


# =========================================================
# BEAT-AWARE TIMING
# =========================================================

def create_timing_grid(
    beat_frames: np.ndarray,
    tempo: float,
    duration: float,
) -> np.ndarray:
    hop_seconds = (
        PITCH_HOP_LENGTH
        / PITCH_SAMPLE_RATE
    )

    beat_times = (
        np.asarray(
            beat_frames,
            dtype=float,
        )
        * hop_seconds
    )

    if len(beat_times) >= 2:
        beat_interval = float(
            np.median(
                np.diff(beat_times)
            )
        )
    else:
        beat_interval = (
            60.0 / max(
                tempo,
                1.0,
            )
        )

    beat_interval = float(
        np.clip(
            beat_interval,
            0.25,
            1.5,
        )
    )

    if len(beat_times):
        first_beat = float(
            beat_times[0]
        )
    else:
        first_beat = 0.0

    while first_beat > 0:
        first_beat -= beat_interval

    if len(beat_times):
        final_beat = float(
            beat_times[-1]
        )
    else:
        final_beat = beat_interval

    while (
        final_beat
        < duration + beat_interval
    ):
        final_beat += beat_interval

    extended_beats = np.arange(
        first_beat,
        final_beat
        + beat_interval * 0.5,
        beat_interval,
    )

    grid: list[float] = []

    for index in range(
        len(extended_beats) - 1
    ):
        beat_start = (
            extended_beats[index]
        )

        for subdivision in range(
            GRID_SUBDIVISIONS_PER_BEAT
        ):
            grid.append(
                beat_start
                + beat_interval
                * subdivision
                / GRID_SUBDIVISIONS_PER_BEAT
            )

    return np.asarray(
        [
            value
            for value in grid
            if value >= 0
        ],
        dtype=np.float64,
    )


def find_nearest_grid_time(
    value: float,
    grid: np.ndarray,
) -> float:
    if grid.size == 0:
        return value

    position = int(
        np.searchsorted(
            grid,
            value,
        )
    )

    candidates: list[float] = []

    if position < len(grid):
        candidates.append(
            float(grid[position])
        )

    if position > 0:
        candidates.append(
            float(
                grid[position - 1]
            )
        )

    if not candidates:
        return value

    return min(
        candidates,
        key=lambda item: abs(
            item - value
        ),
    )


def quantize_note_events(
    events: list[NoteEvent],
    beat_frames: np.ndarray,
    tempo: float,
) -> list[NoteEvent]:
    if not events:
        return []

    timing_grid = create_timing_grid(
        beat_frames,
        tempo,
        events[-1].end,
    )

    for event in events:
        original_start = event.start
        original_end = event.end

        snapped_start = (
            find_nearest_grid_time(
                original_start,
                timing_grid,
            )
        )

        snapped_end = (
            find_nearest_grid_time(
                original_end,
                timing_grid,
            )
        )

        event.start = (
            original_start
            * (1.0 - QUANTIZE_STRENGTH)
            + snapped_start
            * QUANTIZE_STRENGTH
        )

        event.end = (
            original_end
            * (1.0 - QUANTIZE_STRENGTH)
            + snapped_end
            * QUANTIZE_STRENGTH
        )

        if (
            event.end - event.start
            < MINIMUM_NOTE_SECONDS
        ):
            event.end = (
                event.start
                + MINIMUM_NOTE_SECONDS
            )

    final_events: list[NoteEvent] = []

    for event in events:
        if (
            final_events
            and final_events[-1].end
            > event.start
        ):
            final_events[-1].end = (
                event.start - 0.005
            )

            if (
                final_events[-1].duration
                < MINIMUM_NOTE_SECONDS
            ):
                final_events.pop()

        if (
            event.duration
            >= MINIMUM_NOTE_SECONDS
        ):
            final_events.append(event)

    return final_events


# =========================================================
# VELOCITY
# =========================================================

def assign_note_velocities(
    events: list[NoteEvent],
) -> None:
    if not events:
        return

    energies = np.asarray(
        [
            max(
                event.energy,
                1e-9,
            )
            for event in events
        ],
        dtype=np.float64,
    )

    maximum_energy = max(
        float(np.max(energies)),
        1e-9,
    )

    energy_db = (
        20.0
        * np.log10(
            energies / maximum_energy
        )
    )

    lower_level, upper_level = (
        np.percentile(
            energy_db,
            [
                10,
                90,
            ],
        )
    )

    if (
        upper_level
        - lower_level
        < 1e-3
    ):
        upper_level = (
            lower_level + 1.0
        )

    for index, event in enumerate(
        events
    ):
        normalized_level = (
            energy_db[index]
            - lower_level
        ) / (
            upper_level
            - lower_level
        )

        normalized_level = float(
            np.clip(
                normalized_level,
                0.0,
                1.0,
            )
        )

        phrase_accent = (
            5
            if index % 4 == 0
            else 0
        )

        confidence_adjustment = int(
            np.clip(
                (
                    event.confidence
                    - 0.5
                ) * 20,
                -5,
                7,
            )
        )

        event.velocity = int(
            np.clip(
                58
                + normalized_level * 39
                + phrase_accent
                + confidence_adjustment,
                48,
                108,
            )
        )


# =========================================================
# MIDI CREATION
# =========================================================

def add_piano_pedal(
    piano: pretty_midi.Instrument,
    events: list[NoteEvent],
) -> None:
    if not events:
        return

    phrase_start = events[0].start
    phrase_end = events[0].end

    def write_pedal(
        start_time: float,
        end_time: float,
    ) -> None:
        if end_time <= start_time:
            return

        piano.control_changes.append(
            pretty_midi.ControlChange(
                number=64,
                value=58,
                time=max(
                    0.0,
                    start_time,
                ),
            )
        )

        piano.control_changes.append(
            pretty_midi.ControlChange(
                number=64,
                value=0,
                time=max(
                    start_time + 0.03,
                    end_time,
                ),
            )
        )

    for index in range(
        1,
        len(events),
    ):
        previous = events[index - 1]
        current = events[index]

        gap = (
            current.start
            - previous.end
        )

        long_phrase = (
            current.start
            - phrase_start
            > 2.6
        )

        if gap >= 0.18 or long_phrase:
            pedal_end = min(
                phrase_end + 0.05,
                current.start - 0.025,
            )

            write_pedal(
                phrase_start,
                pedal_end,
            )

            phrase_start = (
                current.start
            )

        phrase_end = current.end

    write_pedal(
        phrase_start,
        phrase_end + 0.06,
    )


def create_piano_midi(
    events: list[NoteEvent],
    tempo: float,
    midi_path: Path,
) -> None:
    if len(events) < 2:
        raise PipelineError(
            "পরিষ্কার main melody পাওয়া যায়নি। "
            "আরও স্পষ্ট lead melody-সহ "
            "instrumental ব্যবহার করো।"
        )

    midi = pretty_midi.PrettyMIDI(
        initial_tempo=tempo,
    )

    piano = pretty_midi.Instrument(
        program=0,
        is_drum=False,
        name="TuneMorph Solo Piano",
    )

    for event in events:
        piano.notes.append(
            pretty_midi.Note(
                velocity=event.velocity,
                pitch=event.pitch,
                start=max(
                    0.0,
                    event.start,
                ),
                end=max(
                    event.start
                    + MINIMUM_NOTE_SECONDS,
                    event.end,
                ),
            )
        )

    add_piano_pedal(
        piano,
        events,
    )

    midi.instruments.append(
        piano
    )

    midi.write(
        str(midi_path)
    )


# =========================================================
# PIANO RENDERING
# =========================================================

def render_piano_midi(
    midi_path: Path,
    wav_path: Path,
) -> None:
    fluidsynth = find_executable(
        FLUIDSYNTH_EXE,
        "FluidSynth",
    )

    soundfont = find_soundfont()

    if not soundfont.exists():
        raise PipelineError(
            "Piano SoundFont পাওয়া যায়নি। "
            "এই folder-এ piano.sf2 রাখো: "
            f"{SOUNDFONTS_DIR}"
        )

    command = [
        fluidsynth,

        "-ni",

        # Reverb on
        "-R",
        "1",

        # Chorus off
        "-C",
        "0",

        "-g",
        "0.82",

        "-r",
        "44100",

        "-F",
        str(wav_path),

        str(soundfont),

        str(midi_path),
    ]

    run_command(
        command,
        timeout=1800,
    )


# =========================================================
# WAV + MP3 EXPORT
# =========================================================

def export_final_audio(
    raw_wav_path: Path,
    final_wav_path: Path,
    final_mp3_path: Path,
) -> None:
    ffmpeg = find_executable(
        FFMPEG_EXE,
        "FFmpeg",
    )

    audio_filter = (
        "highpass=f=28,"
        "lowpass=f=18000,"
        "loudnorm=I=-16:"
        "TP=-1.5:"
        "LRA=10"
    )

    # 24-bit WAV
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            str(raw_wav_path),
            "-af",
            audio_filter,
            "-c:a",
            "pcm_s24le",
            str(final_wav_path),
        ],
        timeout=1200,
    )

    # 256 kbps MP3
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            str(final_wav_path),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "256k",
            str(final_mp3_path),
        ],
        timeout=1200,
    )


# =========================================================
# COMPLETE PIPELINE
# =========================================================

def convert_music_to_piano(
    uploaded_audio: Path,
    working_directory: Path,
) -> dict:
    with PIPELINE_LOCK:
        normalized_audio = (
            working_directory
            / "normalized.wav"
        )

        normalize_audio(
            uploaded_audio,
            normalized_audio,
        )

        other_stem = separate_music(
            normalized_audio,
            working_directory
            / "separated",
        )

        (
            original_audio,
            pitch_audio,
            sample_rate,
        ) = prepare_pitch_audio(
            other_stem
        )

        pitch_hz, periodicity = (
            track_pitch(
                pitch_audio,
                sample_rate,
            )
        )

        frame_count = min(
            len(pitch_hz),
            len(periodicity),
        )

        pitch_hz = pitch_hz[
            :frame_count
        ]

        periodicity = periodicity[
            :frame_count
        ]

        (
            rms,
            onset_envelope,
            beat_frames,
            tempo,
        ) = calculate_frame_features(
            original_audio,
            sample_rate,
            frame_count,
        )

        midi_pitch = frequency_to_midi(
            pitch_hz
        )

        labels = smooth_pitch_labels(
            midi_pitch,
            periodicity,
        )

        events = labels_to_note_events(
            labels,
            midi_pitch,
            periodicity,
            rms,
            onset_envelope,
        )

        events = clean_note_events(
            events
        )

        events = quantize_note_events(
            events,
            beat_frames,
            tempo,
        )

        assign_note_velocities(
            events
        )

        midi_path = (
            working_directory
            / "piano.mid"
        )

        raw_wav_path = (
            working_directory
            / "piano_raw.wav"
        )

        final_wav_path = (
            working_directory
            / "piano.wav"
        )

        final_mp3_path = (
            working_directory
            / "piano.mp3"
        )

        create_piano_midi(
            events,
            tempo,
            midi_path,
        )

        render_piano_midi(
            midi_path,
            raw_wav_path,
        )

        export_final_audio(
            raw_wav_path,
            final_wav_path,
            final_mp3_path,
        )

        return {
            "midi": midi_path,
            "wav": final_wav_path,
            "mp3": final_mp3_path,
            "note_count": len(events),
            "tempo": tempo,
        }


# =========================================================
# API ROUTES
# =========================================================

@app.get("/")
def root() -> dict:
    return {
        "message": (
            "TuneMorph Piano API "
            "is running."
        ),
        "docs": "/docs",
    }


@app.get("/api/health")
def health() -> dict:
    soundfont = find_soundfont()

    return {
        "ok": True,

        "ffmpeg_found": (
            shutil.which(
                FFMPEG_EXE
            )
            is not None
            or Path(
                FFMPEG_EXE
            ).exists()
        ),

        "fluidsynth_found": (
            FLUIDSYNTH_EXE.exists()
        ),

        "soundfont_found": (
            soundfont.exists()
        ),

        "soundfont": str(
            soundfont
        ),

        "demucs_found": (
            importlib.util.find_spec(
                "demucs"
            )
            is not None
        ),

        "torchcrepe_found": (
            torchcrepe is not None
        ),

        "cuda_available": bool(
            torch is not None
            and torch.cuda.is_available()
        ),

        "demucs_model": (
            DEMUCS_MODEL
        ),
    }


@app.post("/api/convert")
async def convert(
    file: UploadFile = File(...),
) -> dict:
    cleanup_old_outputs()

    filename = safe_filename(
        file.filename
    )

    extension = (
        Path(filename)
        .suffix
        .lower()
    )

    if (
        extension
        not in ALLOWED_EXTENSIONS
    ):
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
        UPLOADS_DIR
        / job_id
    )

    work_directory = (
        WORK_DIR
        / job_id
    )

    output_directory = (
        OUTPUTS_DIR
        / job_id
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

    upload_path = (
        upload_directory
        / filename
    )

    try:
        maximum_bytes = (
            MAX_UPLOAD_MB
            * 1024
            * 1024
        )

        uploaded_bytes = 0

        with upload_path.open(
            "wb"
        ) as destination:
            while True:
                chunk = await file.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                uploaded_bytes += len(
                    chunk
                )

                if (
                    uploaded_bytes
                    > maximum_bytes
                ):
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "File must be smaller "
                            f"than {MAX_UPLOAD_MB} MB."
                        ),
                    )

                destination.write(
                    chunk
                )

        result = await run_in_threadpool(
            convert_music_to_piano,
            upload_path,
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
                float(
                    result["tempo"]
                ),
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
                "Conversion failed: "
                f"{error}"
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

    if (
        output_path.parent
        != expected_parent
        or not output_path.exists()
    ):
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