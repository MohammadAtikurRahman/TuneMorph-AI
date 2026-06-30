import { useEffect, useRef, useState } from "react";

function App() {
  const API_URL = "http://localhost:8000/convert";

  const [audioFiles, setAudioFiles] = useState([]);
  const [currentFileIndex, setCurrentFileIndex] = useState(0);
  const [instrument, setInstrument] = useState("guitar");

  const [convertedTracks, setConvertedTracks] = useState([]);
  const [currentTrackIndex, setCurrentTrackIndex] = useState(-1);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [isPlaying, setIsPlaying] = useState(false);

  const audioRef = useRef(null);
  const objectUrlsRef = useRef([]);

  const instruments = [
    { value: "guitar", label: "Guitar" },
    { value: "piano", label: "Piano" },
    { value: "flute", label: "Flute" },
    { value: "violin", label: "Violin" },
  ];

  const currentFile = audioFiles[currentFileIndex] || null;
  const currentTrack =
    currentTrackIndex >= 0 ? convertedTracks[currentTrackIndex] : null;

  useEffect(() => {
    return () => {
      forceStopAudio();

      objectUrlsRef.current.forEach((url) => {
        URL.revokeObjectURL(url);
      });
    };
  }, []);

  useEffect(() => {
    if (!audioRef.current) return;

    audioRef.current.pause();
    audioRef.current.currentTime = 0;
    setIsPlaying(false);

    if (currentTrack) {
      audioRef.current.src = currentTrack.url;
      audioRef.current.load();
    } else {
      audioRef.current.removeAttribute("src");
      audioRef.current.load();
    }
  }, [currentTrackIndex, convertedTracks]);

  const forceStopAudio = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current.removeAttribute("src");
      audioRef.current.load();
    }

    setIsPlaying(false);
  };

  const stopAudio = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;

      if (currentTrack) {
        audioRef.current.src = currentTrack.url;
        audioRef.current.load();
      }
    }

    setIsPlaying(false);
  };

  const playAudio = async () => {
    if (!currentTrack) {
      setError("Please convert an audio first.");
      return;
    }

    try {
      setError("");

      if (audioRef.current) {
        audioRef.current.src = currentTrack.url;
        await audioRef.current.play();
        setIsPlaying(true);
      }
    } catch (err) {
      setError("Audio play failed. Please click Play again.");
    }
  };

  const pauseAudio = () => {
    if (audioRef.current) {
      audioRef.current.pause();
    }

    setIsPlaying(false);
  };

  const togglePlayPause = () => {
    if (!currentTrack) {
      setError("Please convert an audio first.");
      return;
    }

    if (isPlaying) {
      pauseAudio();
    } else {
      playAudio();
    }
  };

  const handleFileChange = (event) => {
    const files = Array.from(event.target.files || []);
    const audioOnly = files.filter((file) => file.type.startsWith("audio/"));

    if (audioOnly.length === 0) {
      forceStopAudio();
      setAudioFiles([]);
      setCurrentFileIndex(0);
      setError("Please upload audio files only.");
      return;
    }

    forceStopAudio();
    setAudioFiles(audioOnly);
    setCurrentFileIndex(0);
    setError("");
  };

  const handleNextInputFile = () => {
    if (audioFiles.length <= 1) {
      setError("Only one input audio selected.");
      return;
    }

    forceStopAudio();

    setCurrentFileIndex((prev) => {
      if (prev >= audioFiles.length - 1) return 0;
      return prev + 1;
    });

    setError("");
  };

  const handleConvert = async () => {
    if (!currentFile) {
      setError("Please upload an audio file first.");
      return;
    }

    try {
      forceStopAudio();

      setLoading(true);
      setError("");

      const formData = new FormData();
      formData.append("audio", currentFile);
      formData.append("instrument", instrument);

      const response = await fetch(API_URL, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText || "Conversion failed.");
      }

      const audioBlob = await response.blob();
      const url = URL.createObjectURL(audioBlob);
      objectUrlsRef.current.push(url);

      const newTrack = {
        url,
        instrument,
        originalName: currentFile.name,
        outputName: `tunemorph-${instrument}.wav`,
        createdAt: new Date().toLocaleTimeString(),
      };

      setConvertedTracks((prev) => {
        const updated = [...prev, newTrack];
        setCurrentTrackIndex(updated.length - 1);
        return updated;
      });

      setIsPlaying(false);
    } catch (err) {
      setError(err.message || "Something went wrong.");
    } finally {
      setLoading(false);
    }
  };

  const handleNextConvertedTrack = () => {
    if (convertedTracks.length <= 1) {
      setError("Only one converted track available.");
      return;
    }

    forceStopAudio();

    setCurrentTrackIndex((prev) => {
      if (prev >= convertedTracks.length - 1) return 0;
      return prev + 1;
    });

    setError("");
  };

  const handlePreviousConvertedTrack = () => {
    if (convertedTracks.length <= 1) {
      setError("Only one converted track available.");
      return;
    }

    forceStopAudio();

    setCurrentTrackIndex((prev) => {
      if (prev <= 0) return convertedTracks.length - 1;
      return prev - 1;
    });

    setError("");
  };

  return (
    <>
      <style>{`
        * {
          box-sizing: border-box;
        }

        body {
          margin: 0;
          font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: #070812;
          color: #ffffff;
        }

        .app-shell {
          min-height: 100vh;
          padding: 24px;
          background:
            radial-gradient(circle at top left, rgba(124, 92, 255, 0.22), transparent 32%),
            radial-gradient(circle at bottom right, rgba(0, 184, 148, 0.14), transparent 28%),
            linear-gradient(135deg, #070812 0%, #10111d 58%, #070812 100%);
        }

        .app-container {
          width: 100%;
          max-width: 1320px;
          margin: 0 auto;
        }

        .topbar {
          height: 74px;
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 20px;
          padding: 0 4px;
        }

        .brand {
          display: flex;
          align-items: center;
          gap: 14px;
        }

        .brand-logo {
          width: 48px;
          height: 48px;
          display: grid;
          place-items: center;
          border-radius: 16px;
          background: linear-gradient(135deg, #7c5cff, #00d8a4);
          box-shadow: 0 18px 36px rgba(124, 92, 255, 0.28);
          font-weight: 950;
          font-size: 20px;
        }

        .brand-title {
          margin: 0;
          font-size: 21px;
          letter-spacing: -0.04em;
        }

        .brand-subtitle {
          margin: 3px 0 0;
          color: #8f93a8;
          font-size: 13px;
        }

        .top-actions {
          display: flex;
          align-items: center;
          gap: 12px;
        }

        .status-pill {
          padding: 9px 13px;
          border-radius: 999px;
          color: #aeb4c8;
          background: rgba(255,255,255,0.045);
          border: 1px solid rgba(255,255,255,0.08);
          font-size: 13px;
          font-weight: 700;
        }

        .main-layout {
          display: grid;
          grid-template-columns: 430px minmax(0, 1fr);
          gap: 20px;
          align-items: stretch;
        }

        .panel {
          min-height: calc(100vh - 120px);
          border-radius: 28px;
          background: rgba(18, 20, 34, 0.86);
          border: 1px solid rgba(255,255,255,0.09);
          box-shadow: 0 24px 80px rgba(0,0,0,0.38);
          backdrop-filter: blur(18px);
          overflow: hidden;
        }

        .left-panel {
          padding: 26px;
          display: flex;
          flex-direction: column;
        }

        .right-panel {
          padding: 26px;
          display: flex;
          flex-direction: column;
        }

        .section-kicker {
          display: inline-flex;
          width: max-content;
          padding: 8px 12px;
          border-radius: 999px;
          background: rgba(124, 92, 255, 0.14);
          border: 1px solid rgba(124, 92, 255, 0.28);
          color: #d8d2ff;
          font-size: 12px;
          font-weight: 850;
          margin-bottom: 18px;
        }

        .title {
          margin: 0;
          font-size: 38px;
          line-height: 1;
          letter-spacing: -0.06em;
        }

        .gradient-text {
          background: linear-gradient(135deg, #ffffff, #b9b1ff 56%, #8df5dc);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
        }

        .description {
          margin: 16px 0 26px;
          color: #a9aec4;
          line-height: 1.65;
          font-size: 14px;
        }

        .form-stack {
          display: grid;
          gap: 18px;
        }

        .field-label {
          display: block;
          margin-bottom: 8px;
          color: #e7e9f6;
          font-size: 13px;
          font-weight: 850;
        }

        .upload-card {
          display: block;
          cursor: pointer;
          padding: 22px;
          border-radius: 22px;
          background: rgba(8, 9, 18, 0.76);
          border: 1px dashed rgba(255,255,255,0.22);
          transition: 0.2s ease;
        }

        .upload-card:hover {
          border-color: rgba(124, 92, 255, 0.72);
          background: rgba(124, 92, 255, 0.08);
        }

        .upload-card input {
          display: none;
        }

        .upload-inner {
          display: flex;
          align-items: center;
          gap: 14px;
        }

        .upload-icon {
          width: 48px;
          height: 48px;
          border-radius: 16px;
          display: grid;
          place-items: center;
          background: rgba(124, 92, 255, 0.17);
          color: #d8d2ff;
          font-size: 23px;
        }

        .upload-title {
          margin: 0;
          font-weight: 900;
          color: #ffffff;
        }

        .upload-hint {
          margin: 4px 0 0;
          color: #858aa0;
          font-size: 12px;
        }

        .selected-card {
          padding: 15px;
          border-radius: 18px;
          background: rgba(255,255,255,0.045);
          border: 1px solid rgba(255,255,255,0.08);
        }

        .selected-label {
          color: #858aa0;
          font-size: 12px;
          margin-bottom: 5px;
        }

        .selected-name {
          color: #ffffff;
          font-size: 14px;
          font-weight: 850;
          word-break: break-word;
          line-height: 1.45;
        }

        .selected-meta {
          margin-top: 6px;
          color: #8f93a8;
          font-size: 12px;
        }

        .select-input {
          width: 100%;
          padding: 14px 15px;
          border-radius: 16px;
          border: 1px solid rgba(255,255,255,0.11);
          background: rgba(8, 9, 18, 0.82);
          color: #ffffff;
          outline: none;
          font-size: 14px;
        }

        .convert-button {
          width: 100%;
          margin-top: 4px;
          padding: 16px 18px;
          border: 0;
          border-radius: 18px;
          color: #ffffff;
          background: linear-gradient(135deg, #7c5cff, #5b4bdb);
          box-shadow: 0 18px 38px rgba(124, 92, 255, 0.30);
          font-weight: 950;
          font-size: 15px;
          cursor: pointer;
          transition: 0.18s ease;
        }

        .convert-button:hover {
          transform: translateY(-1px);
          box-shadow: 0 22px 46px rgba(124, 92, 255, 0.36);
        }

        .convert-button:disabled {
          opacity: 0.62;
          cursor: not-allowed;
          transform: none;
          box-shadow: none;
        }

        .secondary-button {
          padding: 12px 14px;
          border-radius: 14px;
          border: 1px solid rgba(255,255,255,0.10);
          background: rgba(255,255,255,0.055);
          color: #e9ebf8;
          font-weight: 850;
          cursor: pointer;
        }

        .button-row {
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
          margin-top: 12px;
        }

        .status-box {
          padding: 13px 14px;
          border-radius: 16px;
          color: #dedcff;
          background: rgba(124, 92, 255, 0.12);
          border: 1px solid rgba(124, 92, 255, 0.22);
          font-size: 13px;
          font-weight: 750;
        }

        .error-box {
          padding: 13px 14px;
          border-radius: 16px;
          color: #ffb6b6;
          background: rgba(255, 118, 117, 0.10);
          border: 1px solid rgba(255, 118, 117, 0.22);
          font-size: 13px;
          font-weight: 800;
          white-space: pre-wrap;
        }

        .tips-card {
          margin-top: auto;
          padding: 17px;
          border-radius: 20px;
          background: rgba(0, 184, 148, 0.08);
          border: 1px solid rgba(0, 184, 148, 0.14);
        }

        .tips-title {
          margin: 0 0 8px;
          font-weight: 900;
          color: #b7ffed;
          font-size: 14px;
        }

        .tips-text {
          margin: 0;
          color: #95a9a4;
          font-size: 12px;
          line-height: 1.6;
        }

        .right-header {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 16px;
          margin-bottom: 22px;
        }

        .right-title {
          margin: 0;
          font-size: 26px;
          letter-spacing: -0.04em;
        }

        .right-subtitle {
          margin: 7px 0 0;
          color: #8f93a8;
          font-size: 13px;
          line-height: 1.5;
        }

        .output-count {
          flex: 0 0 auto;
          padding: 9px 12px;
          border-radius: 999px;
          background: rgba(255,255,255,0.055);
          border: 1px solid rgba(255,255,255,0.08);
          color: #b8bdd3;
          font-size: 12px;
          font-weight: 850;
        }

        .empty-state {
          flex: 1;
          min-height: 430px;
          display: grid;
          place-items: center;
          border-radius: 24px;
          background:
            linear-gradient(135deg, rgba(124, 92, 255, 0.10), rgba(0, 184, 148, 0.05)),
            rgba(8, 9, 18, 0.68);
          border: 1px solid rgba(255,255,255,0.08);
          text-align: center;
          padding: 24px;
        }

        .empty-icon {
          width: 76px;
          height: 76px;
          margin: 0 auto 18px;
          display: grid;
          place-items: center;
          border-radius: 26px;
          background: rgba(124, 92, 255, 0.16);
          color: #d8d2ff;
          font-size: 34px;
        }

        .empty-title {
          margin: 0;
          font-size: 24px;
          font-weight: 950;
          letter-spacing: -0.04em;
        }

        .empty-text {
          max-width: 430px;
          margin: 10px auto 0;
          color: #8f93a8;
          font-size: 14px;
          line-height: 1.65;
        }

        .player-card {
          flex: 1;
          min-height: 430px;
          border-radius: 24px;
          background: rgba(8, 9, 18, 0.74);
          border: 1px solid rgba(255,255,255,0.08);
          padding: 24px;
          display: flex;
          flex-direction: column;
          justify-content: space-between;
        }

        .track-main {
          display: flex;
          gap: 18px;
          align-items: flex-start;
        }

        .album-art {
          width: 120px;
          height: 120px;
          flex: 0 0 auto;
          border-radius: 28px;
          background:
            radial-gradient(circle at 30% 20%, rgba(255,255,255,0.28), transparent 22%),
            linear-gradient(135deg, #7c5cff, #00b894);
          box-shadow: 0 24px 54px rgba(124, 92, 255, 0.28);
          display: grid;
          place-items: center;
          font-size: 42px;
          font-weight: 950;
        }

        .track-info {
          min-width: 0;
        }

        .track-title {
          margin: 0;
          font-size: 21px;
          font-weight: 950;
          line-height: 1.35;
          word-break: break-word;
        }

        .track-meta {
          margin-top: 10px;
          color: #8f93a8;
          font-size: 13px;
          line-height: 1.7;
        }

        .instrument-badge {
          display: inline-flex;
          margin-top: 14px;
          padding: 8px 11px;
          border-radius: 999px;
          color: #b7ffed;
          background: rgba(0, 184, 148, 0.10);
          border: 1px solid rgba(0, 184, 148, 0.16);
          font-size: 12px;
          font-weight: 900;
        }

        .player-controls {
          margin-top: 28px;
          padding-top: 22px;
          border-top: 1px solid rgba(255,255,255,0.08);
        }

        .control-row {
          display: flex;
          gap: 12px;
          flex-wrap: wrap;
          align-items: center;
        }

        .play-button {
          min-width: 116px;
          padding: 14px 18px;
          border: 0;
          border-radius: 16px;
          color: #05120e;
          background: linear-gradient(135deg, #00d8a4, #8df5dc);
          font-weight: 950;
          cursor: pointer;
        }

        .download-button {
          display: inline-flex;
          text-decoration: none;
          align-items: center;
          justify-content: center;
          padding: 14px 18px;
          border-radius: 16px;
          color: #ffffff;
          background: linear-gradient(135deg, #7c5cff, #5b4bdb);
          font-weight: 950;
        }

        .history-strip {
          margin-top: 18px;
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 10px;
        }

        .history-card {
          padding: 12px;
          border-radius: 16px;
          background: rgba(255,255,255,0.045);
          border: 1px solid rgba(255,255,255,0.07);
          min-height: 66px;
        }

        .history-title {
          margin: 0;
          color: #ffffff;
          font-size: 12px;
          font-weight: 850;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        .history-meta {
          margin-top: 5px;
          color: #858aa0;
          font-size: 11px;
        }

        @media (max-width: 980px) {
          .main-layout {
            grid-template-columns: 1fr;
          }

          .panel {
            min-height: auto;
          }
        }

        @media (max-width: 640px) {
          .app-shell {
            padding: 14px;
          }

          .topbar {
            height: auto;
            align-items: flex-start;
            flex-direction: column;
            gap: 12px;
          }

          .left-panel,
          .right-panel {
            padding: 20px;
            border-radius: 22px;
          }

          .title {
            font-size: 34px;
          }

          .track-main {
            flex-direction: column;
          }

          .album-art {
            width: 100%;
            height: 130px;
          }

          .history-strip {
            grid-template-columns: 1fr 1fr;
          }
        }
      `}</style>

      <main className="app-shell">
        <div className="app-container">
          <header className="topbar">
            <div className="brand">
              <div className="brand-logo">T</div>
              <div>
                <h2 className="brand-title">TuneMorph AI</h2>
                <p className="brand-subtitle">Instrumental conversion studio</p>
              </div>
            </div>

            <div className="top-actions">
              <div className="status-pill">
                {loading ? "Processing audio..." : "Ready"}
              </div>
            </div>
          </header>

          <div className="main-layout">
            <section className="panel left-panel">
              <span className="section-kicker">AI Music Tool</span>

              <h1 className="title">
                Same melody.
                <br />
                <span className="gradient-text">New instrument.</span>
              </h1>

              <p className="description">
                Upload clean instrumental audio and convert the melody into
                guitar, piano, flute, or violin.
              </p>

              <div className="form-stack">
                <div>
                  <label className="field-label">Upload Audio</label>

                  <label className="upload-card">
                    <input
                      type="file"
                      accept="audio/*"
                      multiple
                      onChange={handleFileChange}
                    />

                    <div className="upload-inner">
                      <div className="upload-icon">♪</div>
                      <div>
                        <p className="upload-title">Choose audio file</p>
                        <p className="upload-hint">
                          MP3, WAV, FLAC or browser-supported audio
                        </p>
                      </div>
                    </div>
                  </label>
                </div>

                {currentFile && (
                  <div className="selected-card">
                    <div className="selected-label">Selected Input</div>
                    <div className="selected-name">{currentFile.name}</div>
                    <div className="selected-meta">
                      File {currentFileIndex + 1} of {audioFiles.length}
                    </div>

                    <div className="button-row">
                      <button
                        className="secondary-button"
                        type="button"
                        onClick={handleNextInputFile}
                      >
                        Next Input
                      </button>
                    </div>
                  </div>
                )}

                <div>
                  <label className="field-label">Output Instrument</label>
                  <select
                    className="select-input"
                    value={instrument}
                    onChange={(e) => setInstrument(e.target.value)}
                  >
                    {instruments.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </div>

                <button
                  className="convert-button"
                  type="button"
                  onClick={handleConvert}
                  disabled={loading}
                >
                  {loading ? "Converting..." : "Convert Audio"}
                </button>

                {error && <div className="error-box">{error}</div>}

                {loading && (
                  <div className="status-box">
                    Processing audio. Previous playback has been stopped.
                  </div>
                )}
              </div>

              <div className="tips-card">
                <p className="tips-title">Best input</p>
                <p className="tips-text">
                  Clean solo melody gives the best output. Heavy reverb,
                  drums, bass, or full orchestra can create wrong MIDI notes.
                </p>
              </div>
            </section>

            <section className="panel right-panel">
              <div className="right-header">
                <div>
                  <h2 className="right-title">Converted Output</h2>
                  <p className="right-subtitle">
                    Play, stop, switch, and download rendered WAV files.
                  </p>
                </div>

                <div className="output-count">
                  {convertedTracks.length} Output
                </div>
              </div>

              <audio
                ref={audioRef}
                onEnded={() => {
                  setIsPlaying(false);
                }}
              />

              {!currentTrack && (
                <div className="empty-state">
                  <div>
                    <div className="empty-icon">♫</div>
                    <h3 className="empty-title">No output yet</h3>
                    <p className="empty-text">
                      Upload an instrumental audio file, choose an instrument,
                      then convert. Your playable WAV output will appear here.
                    </p>
                  </div>
                </div>
              )}

              {currentTrack && (
                <>
                  <div className="player-card">
                    <div>
                      <div className="track-main">
                        <div className="album-art">
                          {currentTrack.instrument.slice(0, 1).toUpperCase()}
                        </div>

                        <div className="track-info">
                          <p className="track-title">
                            {currentTrack.originalName}
                          </p>

                          <div className="track-meta">
                            Created: {currentTrack.createdAt}
                            <br />
                            Track {currentTrackIndex + 1} of{" "}
                            {convertedTracks.length}
                          </div>

                          <div className="instrument-badge">
                            {currentTrack.instrument.toUpperCase()} VERSION
                          </div>
                        </div>
                      </div>
                    </div>

                    <div className="player-controls">
                      <div className="control-row">
                        <button
                          className="play-button"
                          type="button"
                          onClick={togglePlayPause}
                        >
                          {isPlaying ? "Pause" : "Play"}
                        </button>

                        <button
                          className="secondary-button"
                          type="button"
                          onClick={stopAudio}
                        >
                          Stop
                        </button>

                        <button
                          className="secondary-button"
                          type="button"
                          onClick={handlePreviousConvertedTrack}
                        >
                          Previous
                        </button>

                        <button
                          className="secondary-button"
                          type="button"
                          onClick={handleNextConvertedTrack}
                        >
                          Next
                        </button>

                        <a
                          className="download-button"
                          href={currentTrack.url}
                          download={currentTrack.outputName}
                        >
                          Download WAV
                        </a>
                      </div>
                    </div>
                  </div>

                  {convertedTracks.length > 0 && (
                    <div className="history-strip">
                      {convertedTracks.slice(-4).map((track, index) => (
                        <div className="history-card" key={`${track.url}-${index}`}>
                          <p className="history-title">{track.originalName}</p>
                          <div className="history-meta">
                            {track.instrument} · {track.createdAt}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </section>
          </div>
        </div>
      </main>
    </>
  );
}

export default App;