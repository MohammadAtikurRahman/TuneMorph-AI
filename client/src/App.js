import React, { useState } from "react";

const API_BASE =
  process.env.REACT_APP_API_URL || "http://localhost:8000";

function App() {
  const [audioFile, setAudioFile] = useState(null);
  const [isConverting, setIsConverting] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const handleFileChange = (event) => {
    const selectedFile = event.target.files?.[0] || null;

    setAudioFile(selectedFile);
    setStatus("");
    setError("");
    setResult(null);
  };

  const handleConvert = async () => {
    if (!audioFile) {
      setError("আগে একটি music file select করো।");
      return;
    }

    setIsConverting(true);
    setError("");
    setResult(null);
    setStatus(
      "Music থেকে melody detect করে piano version তৈরি করা হচ্ছে..."
    );

    const formData = new FormData();

    formData.append("file", audioFile);

    // তুমি শুধু instrumental music upload করবে।
    formData.append("mode", "instrumental");

    // Piano melody extraction-এর default settings।
    formData.append("onset_threshold", "0.58");
    formData.append("frame_threshold", "0.34");
    formData.append("minimum_note_ms", "110");
    formData.append("quantize_strength", "0.30");

    try {
      const response = await fetch(
        `${API_BASE}/api/convert`,
        {
          method: "POST",
          body: formData,
        }
      );

      const data = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(
          data?.detail ||
            `Conversion failed: HTTP ${response.status}`
        );
      }

      setResult(data);
      setStatus("Piano version তৈরি হয়েছে।");
    } catch (requestError) {
      console.error(requestError);

      setStatus("");
      setError(
        requestError?.message ||
          "Music convert করা যায়নি। Backend terminal check করো।"
      );
    } finally {
      setIsConverting(false);
    }
  };

  return (
    <>
      <style>{styles}</style>

      <main className="app-page">
        <section className="hero">
          <div className="logo">♫</div>

          <p className="brand-name">
            The Piano
          </p>

          <h1>
            Turn your music into piano.
          </h1>

          <p className="subtitle">
            Music upload করো। The Piano মূল melody খুঁজে
            সেটিকে solo piano হিসেবে তৈরি করবে।
          </p>
        </section>

        <section className="converter-card">
          <label
            className={`upload-box ${
              audioFile ? "file-selected" : ""
            }`}
            htmlFor="audio-file"
          >
            <input
              id="audio-file"
              type="file"
              accept=".mp3,.wav,.flac,.ogg,.m4a,.aac,.wma,audio/*"
              onChange={handleFileChange}
              disabled={isConverting}
            />

            <div className="upload-icon">
              {audioFile ? "✓" : "↑"}
            </div>

            <strong>
              {audioFile
                ? audioFile.name
                : "Choose your music"}
            </strong>

            <span>
              {audioFile
                ? `${(
                    audioFile.size /
                    1024 /
                    1024
                  ).toFixed(2)} MB`
                : "MP3, WAV, FLAC, OGG or M4A"}
            </span>
          </label>

          <button
            type="button"
            className="convert-button"
            onClick={handleConvert}
            disabled={!audioFile || isConverting}
          >
            {isConverting ? (
              <>
                <span className="spinner" />
                Creating Piano Version...
              </>
            ) : (
              "Create Piano Version"
            )}
          </button>

          {status && (
            <div className="status-message">
              {isConverting && (
                <span className="status-dot" />
              )}

              <span>{status}</span>
            </div>
          )}

          {error && (
            <div className="error-message">
              <strong>Conversion failed</strong>
              <span>{error}</span>
            </div>
          )}

          {result && (
            <section className="result-card">
              <div>
                <p className="result-label">
                  PIANO RESULT
                </p>

                <h2>
                  Your piano version is ready
                </h2>

                <p className="result-details">
                  {result.note_count} piano notes
                  {" · "}
                  Estimated {result.estimated_tempo} BPM
                </p>
              </div>

              <audio
                className="audio-player"
                controls
                preload="metadata"
                src={`${API_BASE}${result.audio_url}`}
              >
                Your browser does not support audio playback.
              </audio>

              <div className="download-buttons">
                <a
                  href={`${API_BASE}${result.audio_url}`}
                  download="tunemorph-piano.mp3"
                >
                  Download MP3
                </a>

                <a
                  href={`${API_BASE}${result.wav_url}`}
                  download="tunemorph-piano.wav"
                >
                  Download WAV
                </a>

                <a
                  href={`${API_BASE}${result.midi_url}`}
                  download="tunemorph-piano.mid"
                >
                  Download MIDI
                </a>
              </div>

              <button
                type="button"
                className="new-conversion-button"
                onClick={() => {
                  setAudioFile(null);
                  setResult(null);
                  setStatus("");
                  setError("");

                  const input =
                    document.getElementById("audio-file");

                  if (input) {
                    input.value = "";
                  }
                }}
              >
                Convert Another Music
              </button>
            </section>
          )}
        </section>

        <p className="footer-note">
          Best result-এর জন্য পরিষ্কার instrumental music
          ব্যবহার করো, যেখানে main melody স্পষ্ট শোনা যায়।
        </p>
      </main>
    </>
  );
}

const styles = `
  :root {
    font-family:
      Inter,
      Arial,
      Helvetica,
      sans-serif;

    color: #182033;
    background: #eef2f7;
  }

  * {
    box-sizing: border-box;
  }

  body {
    min-width: 320px;
    min-height: 100vh;
    margin: 0;

    background:
      radial-gradient(
        circle at top left,
        #e6e9ff 0,
        transparent 35rem
      ),
      linear-gradient(
        180deg,
        #f8fafc 0%,
        #edf1f6 100%
      );
  }

  button,
  input {
    font: inherit;
  }

  .app-page {
    width: min(760px, calc(100% - 30px));
    margin: 0 auto;
    padding: 65px 0 80px;
  }

  .hero {
    margin-bottom: 34px;
    text-align: center;
  }

  .logo {
    display: grid;

    width: 62px;
    height: 62px;
    margin: 0 auto 18px;

    place-items: center;

    border-radius: 19px;

    background: #151d2f;
    color: white;

    font-size: 30px;

    box-shadow:
      0 16px 40px
      rgba(21, 29, 47, 0.22);
  }

  .brand-name,
  .result-label {
    margin: 0 0 10px;

    color: #5d5bd7;

    font-size: 12px;
    font-weight: 800;
    letter-spacing: 3px;
  }

  .hero h1 {
    margin: 0;

    font-size: clamp(
      42px,
      8vw,
      50px
    );

    line-height: 1;
    letter-spacing: -4px;
  }

  .subtitle {
    max-width: 570px;
    margin: 22px auto 0;

    color: #626e7e;

    font-size: 17px;
    line-height: 1.7;
  }

  .converter-card {
    padding: clamp(
      22px,
      5vw,
      42px
    );

    border:
      1px solid
      rgba(148, 163, 184, 0.28);

    border-radius: 28px;

    background:
      rgba(255, 255, 255, 0.94);

    box-shadow:
      0 24px 80px
      rgba(31, 41, 55, 0.1);
  }

  .upload-box {
    display: grid;

    min-height: 230px;
    padding: 30px;

    cursor: pointer;

    place-items: center;
    align-content: center;

    gap: 10px;

    border:
      2px dashed #aab4c4;

    border-radius: 22px;

    background: #f8fafc;

    text-align: center;

    transition:
      transform 160ms ease,
      border-color 160ms ease,
      background 160ms ease;
  }

  .upload-box:hover {
    transform: translateY(-2px);

    border-color: #6563da;
    background: #f4f4ff;
  }

  .file-selected {
    border-style: solid;
    border-color: #55a676;
    background: #f1faf5;
  }

  .upload-box input {
    position: absolute;

    width: 1px;
    height: 1px;

    opacity: 0;
    pointer-events: none;
  }

  .upload-icon {
    display: grid;

    width: 54px;
    height: 54px;

    place-items: center;

    border-radius: 16px;

    background: white;
    color: #5553ce;

    font-size: 25px;

    box-shadow:
      0 8px 24px
      rgba(31, 41, 55, 0.11);
  }

  .upload-box strong {
    max-width: 100%;

    font-size: 17px;
    overflow-wrap: anywhere;
  }

  .upload-box span {
    color: #6b7686;
    font-size: 14px;
  }

  .convert-button {
    display: flex;

    width: 100%;
    min-height: 60px;
    margin-top: 24px;

    cursor: pointer;

    align-items: center;
    justify-content: center;

    gap: 10px;

    border: 0;
    border-radius: 17px;

    background: #151d2f;
    color: white;

    font-weight: 800;

    box-shadow:
      0 14px 34px
      rgba(21, 29, 47, 0.2);

    transition:
      transform 150ms ease,
      opacity 150ms ease;
  }

  .convert-button:hover:not(:disabled) {
    transform: translateY(-2px);
  }

  .convert-button:disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }

  .spinner {
    width: 19px;
    height: 19px;

    border:
      2px solid
      rgba(255, 255, 255, 0.28);

    border-top-color: white;
    border-radius: 50%;

    animation:
      spin 0.8s linear infinite;
  }

  .status-message,
  .error-message {
    margin-top: 22px;
    padding: 16px 18px;

    border-radius: 15px;
  }

  .status-message {
    display: flex;

    align-items: center;

    gap: 10px;

    background: #edf8f1;
    color: #236943;
  }

  .status-dot {
    width: 9px;
    height: 9px;

    border-radius: 50%;

    background: #37a966;

    animation:
      pulse 1.1s ease-in-out
      infinite;
  }

  .error-message {
    display: grid;

    gap: 5px;

    background: #fff0f2;
    color: #aa3046;
  }

  .result-card {
    display: grid;

    gap: 21px;

    margin-top: 25px;
    padding: 25px;

    border-radius: 21px;

    background: #111827;
    color: white;
  }

  .result-card h2 {
    margin: 0 0 7px;
  }

  .result-details {
    margin: 0;

    color: #b8c1cf;

    line-height: 1.5;
  }

  .audio-player {
    width: 100%;
  }

  .download-buttons {
    display: grid;

    grid-template-columns:
      repeat(3, 1fr);

    gap: 10px;
  }

  .download-buttons a {
    padding: 13px 14px;

    border:
      1px solid
      rgba(255, 255, 255, 0.16);

    border-radius: 12px;

    background:
      rgba(255, 255, 255, 0.08);

    color: white;

    text-align: center;
    text-decoration: none;

    font-size: 14px;
    font-weight: 750;
  }

  .new-conversion-button {
    padding: 12px;

    cursor: pointer;

    border:
      1px solid
      rgba(255, 255, 255, 0.16);

    border-radius: 12px;

    background: transparent;
    color: #d6dbea;

    font-weight: 700;
  }

  .footer-note {
    max-width: 620px;
    margin: 22px auto 0;

    color: #687486;

    font-size: 14px;
    line-height: 1.6;
    text-align: center;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  @keyframes pulse {
    50% {
      opacity: 0.35;
    }
  }

  @media (max-width: 640px) {
    .app-page {
      width: calc(100% - 20px);
      padding-top: 38px;
    }

    .hero h1 {
      letter-spacing: -2px;
    }

    .converter-card {
      border-radius: 22px;
    }

    .download-buttons {
      grid-template-columns: 1fr;
    }
  }
`;

export default App;