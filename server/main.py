from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import os
import uuid
import shutil
import subprocess
from pathlib import Path

import pretty_midi

from basic_pitch.inference import predict_and_save
from basic_pitch import ICASSP_2022_MODEL_PATH


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent

UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
SOUNDFONT_DIR = BASE_DIR / "soundfonts"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
SOUNDFONT_DIR.mkdir(exist_ok=True)

FLUIDSYNTH_EXE = (
    BASE_DIR
    / "tools"
    / "fluidsynth-v2.5.5-win10-x64-glib"
    / "bin"
    / "fluidsynth.exe"
)

DEFAULT_SOUNDFONT_PATH = SOUNDFONT_DIR / "FluidR3_GM.sf2"

ALLOWED_INSTRUMENTS = ["guitar", "piano", "flute", "violin"]


@app.get("/")
def home():
    return {
        "message": "TuneMorph backend is running",
        "status": "ok",
        "mode": "audio-convert-only"
    }


def get_soundfont_path(instrument_name: str):
    instrument_name = instrument_name.lower().strip()

    specific_soundfonts = {
        "guitar": "guitar.sf2",
        "piano": "piano.sf2",
        "flute": "flute.sf2",
        "violin": "violin.sf2",
    }

    file_name = specific_soundfonts.get(instrument_name)
    specific_path = SOUNDFONT_DIR / file_name if file_name else None

    if specific_path and specific_path.exists():
        return specific_path, True

    if DEFAULT_SOUNDFONT_PATH.exists():
        return DEFAULT_SOUNDFONT_PATH, False

    raise Exception(
        "No SoundFont found. Put guitar.sf2, piano.sf2, flute.sf2, violin.sf2 or FluidR3_GM.sf2 inside server/soundfonts/"
    )


def get_instrument_program(instrument_name: str, using_specific_soundfont: bool) -> int:
    instrument_name = instrument_name.lower().strip()

    # Custom single-instrument sf2 হলে program 0
    if using_specific_soundfont:
        return 0

    # FluidR3_GM fallback হলে GM program
    program_map = {
        "guitar": "Acoustic Guitar (steel)",
        "piano": "Acoustic Grand Piano",
        "flute": "Flute",
        "violin": "Violin",
    }

    gm_name = program_map.get(instrument_name, "Acoustic Grand Piano")

    try:
        return pretty_midi.instrument_name_to_program(gm_name)
    except Exception:
        return pretty_midi.instrument_name_to_program("Acoustic Grand Piano")


def merge_non_drum_instruments(midi_data: pretty_midi.PrettyMIDI):
    """
    Basic Pitch sometimes multiple tracks বানায়।
    সব non-drum note এক track-এ রাখলে render cleaner হয়।
    """
    merged = pretty_midi.PrettyMIDI()
    main_instrument = pretty_midi.Instrument(program=0, name="TuneMorph Main")

    all_notes = []

    for midi_instrument in midi_data.instruments:
        if midi_instrument.is_drum:
            continue

        for note in midi_instrument.notes:
            all_notes.append(
                pretty_midi.Note(
                    velocity=note.velocity,
                    pitch=note.pitch,
                    start=note.start,
                    end=note.end,
                )
            )

    all_notes.sort(key=lambda n: (n.start, n.pitch))
    main_instrument.notes = all_notes
    merged.instruments.append(main_instrument)

    return merged


def remove_bad_notes(midi_data: pretty_midi.PrettyMIDI, instrument_name: str):
    """
    Tiny/wrong notes remove করে।
    খুব aggressive না, যাতে melody নষ্ট না হয়।
    """
    instrument_name = instrument_name.lower().strip()

    min_duration_map = {
        "piano": 0.075,
        "guitar": 0.085,
        "flute": 0.105,
        "violin": 0.105,
    }

    min_duration = min_duration_map.get(instrument_name, 0.085)

    for midi_instrument in midi_data.instruments:
        if midi_instrument.is_drum:
            continue

        cleaned_notes = []

        for note in midi_instrument.notes:
            duration = note.end - note.start

            if duration < min_duration:
                continue

            if note.velocity < 25:
                continue

            note.velocity = max(35, min(115, note.velocity))
            cleaned_notes.append(note)

        cleaned_notes.sort(key=lambda n: (n.start, n.pitch))
        midi_instrument.notes = cleaned_notes

    return midi_data


def fix_note_range(midi_data: pretty_midi.PrettyMIDI, instrument_name: str):
    """
    Instrument natural range-এর বাইরে গেলে octave shift করে।
    """
    instrument_name = instrument_name.lower().strip()

    ranges = {
        "guitar": (40, 88),   # E2 to E6
        "piano": (24, 103),
        "flute": (60, 96),    # C4 to C7
        "violin": (55, 100),  # G3 to E7
    }

    low, high = ranges.get(instrument_name, (24, 103))

    for midi_instrument in midi_data.instruments:
        if midi_instrument.is_drum:
            continue

        for note in midi_instrument.notes:
            while note.pitch < low:
                note.pitch += 12

            while note.pitch > high:
                note.pitch -= 12

            note.pitch = max(0, min(127, note.pitch))

    return midi_data


def smooth_velocity(midi_data: pretty_midi.PrettyMIDI, instrument_name: str):
    """
    Uneven loud/soft note balance করে।
    """
    instrument_name = instrument_name.lower().strip()

    velocity_settings = {
        "piano": (45, 105, 76, 0.65),
        "guitar": (42, 94, 68, 0.60),
        "flute": (54, 90, 72, 0.55),
        "violin": (44, 88, 66, 0.55),
    }

    min_v, max_v, center_v, strength = velocity_settings.get(
        instrument_name,
        (45, 100, 72, 0.60)
    )

    for midi_instrument in midi_data.instruments:
        if midi_instrument.is_drum:
            continue

        for note in midi_instrument.notes:
            note.velocity = int(center_v + (note.velocity - center_v) * strength)
            note.velocity = max(min_v, min(max_v, note.velocity))

    return midi_data


def merge_repeated_notes(midi_data: pretty_midi.PrettyMIDI, instrument_name: str):
    """
    Same pitch repeated tiny shaky notes merge করে।
    """
    instrument_name = instrument_name.lower().strip()

    max_gap_map = {
        "piano": 0.040,
        "guitar": 0.050,
        "flute": 0.080,
        "violin": 0.080,
    }

    max_gap = max_gap_map.get(instrument_name, 0.055)

    for midi_instrument in midi_data.instruments:
        if midi_instrument.is_drum:
            continue

        notes = sorted(midi_instrument.notes, key=lambda n: (n.pitch, n.start))
        merged_notes = []
        used = [False] * len(notes)

        for i, note in enumerate(notes):
            if used[i]:
                continue

            current = note
            used[i] = True

            for j in range(i + 1, len(notes)):
                other = notes[j]

                if used[j]:
                    continue

                if other.pitch != current.pitch:
                    break

                gap = other.start - current.end

                if 0 <= gap <= max_gap:
                    current.end = max(current.end, other.end)
                    current.velocity = max(current.velocity, other.velocity)
                    used[j] = True
                elif other.start > current.end + max_gap:
                    break

            merged_notes.append(current)

        merged_notes.sort(key=lambda n: (n.start, n.pitch))
        midi_instrument.notes = merged_notes

    return midi_data


def reduce_polyphony(midi_data: pretty_midi.PrettyMIDI, instrument_name: str):
    """
    Basic Pitch full audio থেকে অনেক extra note তুললে amateur লাগে।
    Piano/Guitar-এ limited polyphony রাখে।
    Flute/Violin monophonic হবে।
    """
    instrument_name = instrument_name.lower().strip()

    if instrument_name in ["flute", "violin"]:
        return midi_data

    max_notes_map = {
        "piano": 4,
        "guitar": 3,
    }

    max_notes = max_notes_map.get(instrument_name, 4)
    group_window = 0.045

    for midi_instrument in midi_data.instruments:
        if midi_instrument.is_drum:
            continue

        notes = sorted(midi_instrument.notes, key=lambda n: (n.start, n.pitch))

        if not notes:
            continue

        groups = []
        current_group = [notes[0]]
        group_start = notes[0].start

        for note in notes[1:]:
            if abs(note.start - group_start) <= group_window:
                current_group.append(note)
            else:
                groups.append(current_group)
                current_group = [note]
                group_start = note.start

        groups.append(current_group)

        selected_notes = []

        for group in groups:
            if len(group) <= max_notes:
                selected_notes.extend(group)
            else:
                ranked = sorted(
                    group,
                    key=lambda n: (
                        n.velocity * 1.2
                        + (n.end - n.start) * 20
                        + n.pitch * 0.08
                    ),
                    reverse=True,
                )
                selected_notes.extend(ranked[:max_notes])

        selected_notes.sort(key=lambda n: (n.start, n.pitch))
        midi_instrument.notes = selected_notes

    return midi_data


def make_monophonic_for_lead(midi_data: pretty_midi.PrettyMIDI, instrument_name: str):
    """
    Flute/Violin lead instrument.
    একসাথে অনেক note বাজলে বাজে লাগে, তাই main melody রাখে।
    """
    instrument_name = instrument_name.lower().strip()

    if instrument_name not in ["flute", "violin"]:
        return midi_data

    group_window = 0.060

    for midi_instrument in midi_data.instruments:
        if midi_instrument.is_drum:
            continue

        notes = sorted(midi_instrument.notes, key=lambda n: (n.start, n.pitch))

        if not notes:
            continue

        groups = []
        current_group = [notes[0]]
        group_start = notes[0].start

        for note in notes[1:]:
            if abs(note.start - group_start) <= group_window:
                current_group.append(note)
            else:
                groups.append(current_group)
                current_group = [note]
                group_start = note.start

        groups.append(current_group)

        melody_notes = []
        previous_pitch = None

        for group in groups:
            if previous_pitch is None:
                best_note = max(
                    group,
                    key=lambda n: (
                        n.velocity * 1.10
                        + (n.end - n.start) * 18
                        + n.pitch * 0.40
                    )
                )
            else:
                best_note = max(
                    group,
                    key=lambda n: (
                        n.velocity * 1.10
                        + (n.end - n.start) * 18
                        + n.pitch * 0.35
                        - abs(n.pitch - previous_pitch) * 0.85
                    )
                )

            melody_notes.append(best_note)
            previous_pitch = best_note.pitch

        melody_notes.sort(key=lambda n: n.start)

        filtered_notes = []

        for note in melody_notes:
            if not filtered_notes:
                filtered_notes.append(note)
                continue

            prev = filtered_notes[-1]
            interval = abs(note.pitch - prev.pitch)
            duration = note.end - note.start

            # Huge random short jump skip
            if interval > 15 and duration < 0.18:
                continue

            filtered_notes.append(note)

        # Overlap clean
        for i in range(len(filtered_notes) - 1):
            current_note = filtered_notes[i]
            next_note = filtered_notes[i + 1]

            if current_note.end > next_note.start - 0.012:
                current_note.end = max(
                    current_note.start + 0.13,
                    next_note.start - 0.012
                )

        midi_instrument.notes = filtered_notes

    return midi_data


def remove_too_fast_notes(midi_data: pretty_midi.PrettyMIDI, instrument_name: str):
    """
    Too fast shaky weak notes remove করে।
    """
    instrument_name = instrument_name.lower().strip()

    min_start_gap_map = {
        "piano": 0.030,
        "guitar": 0.040,
        "flute": 0.060,
        "violin": 0.060,
    }

    min_start_gap = min_start_gap_map.get(instrument_name, 0.045)

    for midi_instrument in midi_data.instruments:
        if midi_instrument.is_drum:
            continue

        notes = sorted(midi_instrument.notes, key=lambda n: n.start)
        filtered = []

        for note in notes:
            if not filtered:
                filtered.append(note)
                continue

            prev = filtered[-1]

            if note.start - prev.start < min_start_gap:
                note_score = note.velocity + (note.end - note.start) * 35
                prev_score = prev.velocity + (prev.end - prev.start) * 35

                if note_score > prev_score + 12:
                    filtered[-1] = note

                continue

            filtered.append(note)

        filtered.sort(key=lambda n: (n.start, n.pitch))
        midi_instrument.notes = filtered

    return midi_data


def apply_instrument_style(midi_data: pretty_midi.PrettyMIDI, instrument_name: str):
    """
    Instrument অনুযায়ী note length adjust.
    """
    instrument_name = instrument_name.lower().strip()

    for midi_instrument in midi_data.instruments:
        if midi_instrument.is_drum:
            continue

        notes = sorted(midi_instrument.notes, key=lambda n: n.start)

        for note in notes:
            duration = note.end - note.start

            if instrument_name == "guitar":
                new_duration = duration * 0.72
                new_duration = max(0.12, min(1.05, new_duration))
                note.end = note.start + new_duration

            elif instrument_name == "piano":
                new_duration = duration * 1.03
                new_duration = max(0.13, min(2.30, new_duration))
                note.end = note.start + new_duration

            elif instrument_name == "flute":
                new_duration = duration * 1.08
                new_duration = max(0.18, min(1.80, new_duration))
                note.end = note.start + new_duration

            elif instrument_name == "violin":
                new_duration = duration * 1.07
                new_duration = max(0.17, min(1.70, new_duration))
                note.end = note.start + new_duration

        if instrument_name in ["flute", "violin"]:
            for i in range(len(notes) - 1):
                if notes[i].end > notes[i + 1].start - 0.010:
                    notes[i].end = max(
                        notes[i].start + 0.12,
                        notes[i + 1].start - 0.010
                    )

        midi_instrument.notes = notes

    return midi_data


def add_expression_controls(midi_data: pretty_midi.PrettyMIDI, instrument_name: str):
    """
    MIDI volume/expression/reverb/chorus control.
    """
    instrument_name = instrument_name.lower().strip()

    settings = {
        "piano": {"volume": 100, "expression": 105, "reverb": 34, "chorus": 5},
        "guitar": {"volume": 104, "expression": 104, "reverb": 30, "chorus": 7},
        "flute": {"volume": 101, "expression": 108, "reverb": 44, "chorus": 6},
        "violin": {"volume": 98, "expression": 107, "reverb": 42, "chorus": 6},
    }

    s = settings.get(
        instrument_name,
        {"volume": 100, "expression": 105, "reverb": 36, "chorus": 5}
    )

    for midi_instrument in midi_data.instruments:
        if midi_instrument.is_drum:
            continue

        midi_instrument.control_changes.append(
            pretty_midi.ControlChange(number=7, value=s["volume"], time=0)
        )
        midi_instrument.control_changes.append(
            pretty_midi.ControlChange(number=11, value=s["expression"], time=0)
        )
        midi_instrument.control_changes.append(
            pretty_midi.ControlChange(number=91, value=s["reverb"], time=0)
        )
        midi_instrument.control_changes.append(
            pretty_midi.ControlChange(number=93, value=s["chorus"], time=0)
        )

    return midi_data


def prepare_midi_for_instrument(
    midi_path: Path,
    prepared_midi_path: Path,
    instrument_name: str,
    selected_program: int
):
    """
    Audio থেকে Basic Pitch MIDI আসার পর clean + instrument styling.
    """
    midi_data = pretty_midi.PrettyMIDI(str(midi_path))

    midi_data = merge_non_drum_instruments(midi_data)
    midi_data = remove_bad_notes(midi_data, instrument_name)
    midi_data = fix_note_range(midi_data, instrument_name)
    midi_data = smooth_velocity(midi_data, instrument_name)
    midi_data = merge_repeated_notes(midi_data, instrument_name)
    midi_data = reduce_polyphony(midi_data, instrument_name)
    midi_data = make_monophonic_for_lead(midi_data, instrument_name)
    midi_data = remove_too_fast_notes(midi_data, instrument_name)
    midi_data = apply_instrument_style(midi_data, instrument_name)
    midi_data = add_expression_controls(midi_data, instrument_name)

    for midi_instrument in midi_data.instruments:
        if not midi_instrument.is_drum:
            midi_instrument.program = selected_program
            midi_instrument.notes.sort(key=lambda n: (n.start, n.pitch))

    midi_data.write(str(prepared_midi_path))


def render_midi_to_wav(midi_path: Path, wav_path: Path, instrument: str):
    if not FLUIDSYNTH_EXE.exists():
        raise Exception(f"FluidSynth not found: {FLUIDSYNTH_EXE}")

    soundfont_path, using_specific_soundfont = get_soundfont_path(instrument)

    if not soundfont_path.exists():
        raise Exception(f"SoundFont not found: {soundfont_path}")

    selected_program = get_instrument_program(
        instrument,
        using_specific_soundfont
    )

    print("Using SoundFont:", soundfont_path)
    print("Specific SoundFont:", using_specific_soundfont)
    print("Selected MIDI program:", selected_program)

    prepared_midi_path = midi_path.with_name(f"prepared-{instrument}.mid")

    prepare_midi_for_instrument(
        midi_path=midi_path,
        prepared_midi_path=prepared_midi_path,
        instrument_name=instrument,
        selected_program=selected_program
    )

    command = [
        str(FLUIDSYNTH_EXE),
        "-ni",
        "-a",
        "file",
        "-F",
        str(wav_path),
        "-T",
        "wav",
        "-r",
        "44100",
        str(soundfont_path),
        str(prepared_midi_path),
    ]

    print("Running FluidSynth command:")
    print(" ".join(command))

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise Exception(
            f"FluidSynth failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )

    if not wav_path.exists():
        raise Exception("WAV file was not created by FluidSynth.")


def normalize_audio(audio, target_peak=0.88):
    import numpy as np

    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)

    peak = float(np.max(np.abs(audio)))

    if peak > 0.00001:
        audio = audio * (target_peak / peak)

    audio = np.clip(audio, -0.98, 0.98)

    return audio


def get_polish_settings(instrument_name: str):
    instrument_name = instrument_name.lower().strip()

    settings = {
        "piano": {
            "highpass": 35,
            "lowpass": 14500,
            "reverb_room": 0.18,
            "reverb_wet": 0.050,
            "compressor_threshold": -20,
            "compressor_ratio": 1.7,
            "target_peak": 0.88,
        },
        "guitar": {
            "highpass": 55,
            "lowpass": 12500,
            "reverb_room": 0.16,
            "reverb_wet": 0.045,
            "compressor_threshold": -21,
            "compressor_ratio": 2.0,
            "target_peak": 0.87,
        },
        "flute": {
            "highpass": 105,
            "lowpass": 15000,
            "reverb_room": 0.26,
            "reverb_wet": 0.070,
            "compressor_threshold": -22,
            "compressor_ratio": 1.6,
            "target_peak": 0.87,
        },
        "violin": {
            "highpass": 80,
            "lowpass": 13500,
            "reverb_room": 0.24,
            "reverb_wet": 0.065,
            "compressor_threshold": -22,
            "compressor_ratio": 1.7,
            "target_peak": 0.86,
        },
    }

    return settings.get(instrument_name, settings["piano"])


def polish_wav(input_wav_path: Path, output_wav_path: Path, instrument_name: str):
    """
    Raw FluidSynth WAV → clean final WAV.
    """
    try:
        import numpy as np
        import soundfile as sf

        from pedalboard import (
            Pedalboard,
            HighpassFilter,
            LowpassFilter,
            Compressor,
            Reverb,
            Limiter,
        )
    except Exception as e:
        raise Exception(
            "Audio polish libraries missing. Run:\n"
            ".\\venv311\\Scripts\\python.exe -m pip install pedalboard soundfile numpy\n"
            f"Original error: {str(e)}"
        )

    if not input_wav_path.exists():
        raise Exception(f"Input WAV not found: {input_wav_path}")

    settings = get_polish_settings(instrument_name)

    audio_data, sample_rate = sf.read(
        str(input_wav_path),
        dtype="float32",
        always_2d=True
    )

    # samples x channels → channels x samples
    audio = audio_data.T
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)

    # pre normalize
    audio = normalize_audio(audio, target_peak=0.68)

    board = Pedalboard([
        HighpassFilter(cutoff_frequency_hz=settings["highpass"]),
        LowpassFilter(cutoff_frequency_hz=settings["lowpass"]),
        Compressor(
            threshold_db=settings["compressor_threshold"],
            ratio=settings["compressor_ratio"],
            attack_ms=14,
            release_ms=140,
        ),
        Reverb(
            room_size=settings["reverb_room"],
            damping=0.45,
            wet_level=settings["reverb_wet"],
            dry_level=0.94,
            width=0.80,
        ),
        Limiter(
            threshold_db=-1.0,
            release_ms=90,
        ),
    ])

    processed = board(audio, sample_rate)

    processed = normalize_audio(
        processed,
        target_peak=settings["target_peak"]
    )

    # channels x samples → samples x channels
    processed = processed.T
    processed = np.clip(processed, -0.98, 0.98)

    sf.write(
        str(output_wav_path),
        processed,
        sample_rate,
        subtype="PCM_16"
    )

    if not output_wav_path.exists():
        raise Exception("Final polished WAV was not created.")


@app.post("/convert")
async def convert_audio(
    audio: UploadFile = File(...),
    instrument: str = Form(...)
):
    instrument = instrument.lower().strip()

    if instrument not in ALLOWED_INSTRUMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported instrument: {instrument}"
        )

    original_name = audio.filename or "uploaded_audio.mp3"
    ext = os.path.splitext(original_name)[1]

    if ext == "":
        ext = ".mp3"

    file_id = str(uuid.uuid4())

    input_path = UPLOAD_DIR / f"{file_id}{ext}"
    job_output_dir = OUTPUT_DIR / file_id
    job_output_dir.mkdir(exist_ok=True)

    with open(input_path, "wb") as buffer:
        shutil.copyfileobj(audio.file, buffer)

    print("===================================")
    print("AUDIO CONVERT MODE ONLY")
    print("Uploaded:", original_name)
    print("Selected instrument:", instrument)
    print("Input path:", input_path)
    print("Output folder:", job_output_dir)

    try:
        print("Starting Basic Pitch MIDI conversion...")

        predict_and_save(
            [str(input_path)],
            str(job_output_dir),
            True,   # save MIDI
            False,  # do not sonify MIDI
            False,  # do not save model outputs
            False,  # do not save note events
            ICASSP_2022_MODEL_PATH,
        )
    except Exception as e:
        print("Basic Pitch error:", str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Basic Pitch failed: {str(e)}"
        )

    midi_files = list(job_output_dir.glob("*.mid")) + list(job_output_dir.glob("*.midi"))

    print("Generated files:")
    for f in job_output_dir.iterdir():
        print("-", f.name)

    if not midi_files:
        raise HTTPException(
            status_code=500,
            detail="MIDI file was not generated. Try clean solo instrumental melody audio."
        )

    midi_path = midi_files[0]

    raw_wav_path = job_output_dir / f"raw-{instrument}.wav"
    final_wav_path = job_output_dir / f"tunemorph-{instrument}-final.wav"

    try:
        print("Rendering MIDI to raw WAV...")
        render_midi_to_wav(midi_path, raw_wav_path, instrument)

        print("Polishing WAV...")
        polish_wav(raw_wav_path, final_wav_path, instrument)
    except Exception as e:
        print("Render/Polish error:", str(e))
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    print("Final WAV generated:", final_wav_path)
    print("===================================")

    return FileResponse(
        path=str(final_wav_path),
        media_type="audio/wav",
        filename=f"tunemorph-{instrument}-final.wav"
    )