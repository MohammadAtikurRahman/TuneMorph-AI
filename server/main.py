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


@app.get("/")
def home():
    return {
        "message": "CoverTone backend is running",
        "status": "ok"
    }


def get_soundfont_path(instrument_name: str):
    """
    Instrument-specific SoundFont থাকলে সেটা use করবে.
    না থাকলে FluidR3_GM.sf2 fallback করবে.
    """
    instrument_name = instrument_name.lower().strip()

    specific_soundfonts = {
        "guitar": "guitar.sf2",
        "piano": "piano.sf2",
        "flute": "flute.sf2",
        "violin": "violin.sf2",
        "cello": "cello.sf2",
    }

    file_name = specific_soundfonts.get(instrument_name)
    specific_path = SOUNDFONT_DIR / file_name if file_name else None

    if specific_path and specific_path.exists():
        return specific_path, True

    if DEFAULT_SOUNDFONT_PATH.exists():
        return DEFAULT_SOUNDFONT_PATH, False

    raise Exception(
        "No SoundFont found. Put FluidR3_GM.sf2 or instrument-specific sf2 files inside server/soundfonts/"
    )


def get_instrument_program(instrument_name: str, using_specific_soundfont: bool) -> int:
    """
    Specific sf2 হলে অনেক সময় program 0 লাগে.
    FluidR3_GM.sf2 fallback হলে proper General MIDI program use করবে.
    """
    instrument_name = instrument_name.lower().strip()

    if using_specific_soundfont:
        return 0

    program_map = {
        "guitar": "Acoustic Guitar (steel)",
        "piano": "Acoustic Grand Piano",
        "flute": "Flute",
        "violin": "Violin",
        "cello": "Cello",
    }

    gm_name = program_map.get(instrument_name, "Acoustic Grand Piano")

    try:
        return pretty_midi.instrument_name_to_program(gm_name)
    except Exception:
        return pretty_midi.instrument_name_to_program("Acoustic Grand Piano")


def clean_midi_notes(midi_data: pretty_midi.PrettyMIDI):
    """
    Basic Pitch অনেক সময় tiny/wrong notes বানায়.
    এই function খুব ছোট note remove করে এবং weak notes একটু boost করে.
    """
    for midi_instrument in midi_data.instruments:
        if midi_instrument.is_drum:
            continue

        cleaned_notes = []

        for note in midi_instrument.notes:
            duration = note.end - note.start

            # Extra tiny wrong notes remove
            if duration < 0.08:
                continue

            # Very low velocity boost
            if note.velocity < 35:
                note.velocity = 45

            # Very high velocity limit
            if note.velocity > 115:
                note.velocity = 115

            cleaned_notes.append(note)

        midi_instrument.notes = cleaned_notes

    return midi_data


def render_midi_to_wav(midi_path: Path, wav_path: Path, instrument: str):
    """
    MIDI -> WAV using FluidSynth.
    guitar.sf2/piano.sf2 থাকলে use করবে.
    না থাকলে FluidR3_GM.sf2 fallback করবে.
    """

    if not FLUIDSYNTH_EXE.exists():
        raise Exception(f"FluidSynth not found: {FLUIDSYNTH_EXE}")

    soundfont_path, using_specific_soundfont = get_soundfont_path(instrument)

    if not soundfont_path.exists():
        raise Exception(f"SoundFont not found: {soundfont_path}")

    print("Using SoundFont:", soundfont_path)
    print("Specific SoundFont:", using_specific_soundfont)

    midi_data = pretty_midi.PrettyMIDI(str(midi_path))

    # Clean tiny wrong notes
    midi_data = clean_midi_notes(midi_data)

    selected_program = get_instrument_program(
        instrument,
        using_specific_soundfont
    )

    for midi_instrument in midi_data.instruments:
        if not midi_instrument.is_drum:
            midi_instrument.program = selected_program

    prepared_midi_path = midi_path.with_name(f"prepared-{instrument}.mid")
    midi_data.write(str(prepared_midi_path))

    command = [
        str(FLUIDSYNTH_EXE),
        "-ni",
        str(soundfont_path),
        str(prepared_midi_path),
        "-F",
        str(wav_path),
        "-r",
        "44100",
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


@app.post("/convert")
async def convert_audio(
    audio: UploadFile = File(...),
    instrument: str = Form(...)
):
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
    print("Uploaded:", original_name)
    print("Selected instrument:", instrument)
    print("Input path:", input_path)
    print("Output folder:", job_output_dir)
    print("Starting Basic Pitch MIDI conversion...")

    try:
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
            detail="MIDI file was not generated. Try a clean solo flute/piano/vocal melody audio."
        )

    midi_path = midi_files[0]
    wav_path = job_output_dir / f"covertone-{instrument}.wav"

    print("MIDI generated:", midi_path)
    print("Rendering MIDI to WAV with FluidSynth...")

    try:
        render_midi_to_wav(midi_path, wav_path, instrument)
    except Exception as e:
        print("MIDI render error:", str(e))
        raise HTTPException(
            status_code=500,
            detail=f"MIDI render failed: {str(e)}"
        )

    print("WAV generated:", wav_path)
    print("===================================")

    return FileResponse(
        path=str(wav_path),
        media_type="audio/wav",
        filename=f"covertone-{instrument}.wav"
    )