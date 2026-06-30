import { useEffect, useRef, useState } from "react";

function App() {
  const [audioFiles, setAudioFiles] = useState([]);
  const [currentFileIndex, setCurrentFileIndex] = useState(0);
  const [instrument, setInstrument] = useState("guitar");

  const [convertedTracks, setConvertedTracks] = useState([]);
  const [currentTrackIndex, setCurrentTrackIndex] = useState(-1);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [isPlaying, setIsPlaying] = useState(false);

  const audioRef = useRef(null);

  const API_URL = "http://localhost:8000/convert";

  const instruments = [
    { value: "guitar", label: "Guitar" },
    { value: "piano", label: "Piano" },
    { value: "flute", label: "Flute" },
    { value: "violin", label: "Violin" },
    { value: "cello", label: "Cello" },
  ];

  const currentFile = audioFiles[currentFileIndex] || null;
  const currentTrack =
    currentTrackIndex >= 0 ? convertedTracks[currentTrackIndex] : null;

  useEffect(() => {
    return () => {
      stopAudio();
      convertedTracks.forEach((track) => {
        URL.revokeObjectURL(track.url);
      });
    };
  }, [convertedTracks]);

  const stopAudio = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
    }

    setIsPlaying(false);
  };

  const pauseAudio = () => {
    if (audioRef.current) {
      audioRef.current.pause();
    }

    setIsPlaying(false);
  };

  const playAudio = async () => {
    if (!currentTrack) {
      setError("No converted track found. Convert an audio first.");
      return;
    }

    try {
      setError("");
      await audioRef.current.play();
      setIsPlaying(true);
    } catch (err) {
      setError("Audio play failed. Please click play again.");
    }
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
      setError("Please upload audio files only.");
      setAudioFiles([]);
      setCurrentFileIndex(0);
      return;
    }

    stopAudio();

    setError("");
    setAudioFiles(audioOnly);
    setCurrentFileIndex(0);
  };

  const handleNextInputFile = () => {
    if (audioFiles.length <= 1) {
      setError("Only one input audio selected.");
      return;
    }

    stopAudio();

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
      stopAudio();

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

      const newTrack = {
        url,
        instrument,
        originalName: currentFile.name,
        outputName: `covertone-${instrument}.wav`,
        createdAt: new Date().toLocaleTimeString(),
      };

      setConvertedTracks((prev) => {
        const updated = [...prev, newTrack];
        setCurrentTrackIndex(updated.length - 1);
        return updated;
      });
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

    stopAudio();

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

    stopAudio();

    setCurrentTrackIndex((prev) => {
      if (prev <= 0) return convertedTracks.length - 1;
      return prev - 1;
    });

    setError("");
  };

  const styles = {
    page: {
      minHeight: "100vh",
      background: "linear-gradient(135deg, #0f1020, #15151f)",
      color: "#ffffff",
      display: "flex",
      justifyContent: "center",
      alignItems: "center",
      padding: "30px",
      fontFamily: "Arial, Helvetica, sans-serif",
    },
    card: {
      width: "100%",
      maxWidth: "720px",
      background: "#1b1b26",
      border: "1px solid #303044",
      borderRadius: "20px",
      padding: "32px",
      boxShadow: "0 25px 70px rgba(0, 0, 0, 0.45)",
    },
    badge: {
      display: "inline-block",
      background: "#292942",
      color: "#bdbdff",
      padding: "7px 12px",
      borderRadius: "999px",
      fontSize: "13px",
      marginBottom: "16px",
    },
    title: {
      margin: "0 0 10px",
      fontSize: "36px",
      lineHeight: "1.1",
    },
    subtitle: {
      margin: "0 0 30px",
      color: "#b7b7c9",
      lineHeight: "1.6",
      fontSize: "16px",
    },
    formGroup: {
      marginBottom: "22px",
    },
    label: {
      display: "block",
      marginBottom: "8px",
      color: "#e1e1ef",
      fontWeight: "700",
    },
    input: {
      width: "100%",
      padding: "14px",
      borderRadius: "12px",
      border: "1px solid #45455d",
      background: "#11111a",
      color: "#ffffff",
      outline: "none",
      fontSize: "15px",
      boxSizing: "border-box",
    },
    select: {
      width: "100%",
      padding: "14px",
      borderRadius: "12px",
      border: "1px solid #45455d",
      background: "#11111a",
      color: "#ffffff",
      outline: "none",
      fontSize: "15px",
      cursor: "pointer",
      boxSizing: "border-box",
    },
    fileBox: {
      background: "#11111a",
      border: "1px solid #33334a",
      padding: "14px",
      borderRadius: "12px",
      marginBottom: "22px",
      color: "#d0d0e3",
      lineHeight: "1.6",
    },
    button: {
      padding: "13px 16px",
      border: "none",
      borderRadius: "12px",
      background: "#6c5ce7",
      color: "#ffffff",
      fontSize: "15px",
      fontWeight: "800",
      cursor: "pointer",
    },
    buttonSecondary: {
      padding: "13px 16px",
      border: "1px solid #45455d",
      borderRadius: "12px",
      background: "#11111a",
      color: "#ffffff",
      fontSize: "15px",
      fontWeight: "800",
      cursor: "pointer",
    },
    convertButton: {
      width: "100%",
      padding: "16px",
      border: "none",
      borderRadius: "14px",
      background: loading ? "#55556e" : "#6c5ce7",
      color: "#ffffff",
      fontSize: "16px",
      fontWeight: "800",
      cursor: loading ? "not-allowed" : "pointer",
      marginTop: "4px",
    },
    buttonRow: {
      display: "flex",
      gap: "10px",
      flexWrap: "wrap",
      marginTop: "14px",
    },
    error: {
      marginTop: "18px",
      color: "#ff7675",
      fontWeight: "700",
      whiteSpace: "pre-wrap",
    },
    status: {
      marginTop: "18px",
      padding: "14px",
      borderRadius: "12px",
      background: "#292942",
      color: "#dedcff",
    },
    resultBox: {
      marginTop: "28px",
      paddingTop: "22px",
      borderTop: "1px solid #33334a",
    },
    playerBox: {
      background: "#11111a",
      border: "1px solid #33334a",
      padding: "18px",
      borderRadius: "14px",
      marginTop: "14px",
    },
    download: {
      display: "inline-block",
      textDecoration: "none",
      background: "#00b894",
      color: "#ffffff",
      padding: "12px 18px",
      borderRadius: "10px",
      fontWeight: "800",
    },
    smallText: {
      color: "#b7b7c9",
      fontSize: "14px",
    },
  };

  return (
    <main style={styles.page}>
      <section style={styles.card}>
        <span style={styles.badge}>AI Music Tool</span>

        <h1 style={styles.title}>TuneMorph AI</h1>

        <p style={styles.subtitle}>
          Same melody, new instrument. Upload instrumental audio and convert it
          into guitar, piano, flute, violin, or cello.
        </p>

        <div style={styles.formGroup}>
          <label style={styles.label}>Upload Audio</label>
          <input
            style={styles.input}
            type="file"
            accept="audio/*"
            multiple
            onChange={handleFileChange}
          />
        </div>

        {currentFile && (
          <div style={styles.fileBox}>
            <strong>Selected Input:</strong>
            <br />
            {currentFile.name}
            <br />
            <span style={styles.smallText}>
              File {currentFileIndex + 1} of {audioFiles.length}
            </span>

            <div style={styles.buttonRow}>
              <button style={styles.buttonSecondary} onClick={handleNextInputFile}>
                Next Input Song
              </button>
            </div>
          </div>
        )}

        <div style={styles.formGroup}>
          <label style={styles.label}>Select Output Instrument</label>
          <select
            style={styles.select}
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
          style={styles.convertButton}
          onClick={handleConvert}
          disabled={loading}
        >
          {loading ? "Converting..." : "Convert Audio"}
        </button>

        {error && <p style={styles.error}>{error}</p>}

        {loading && (
          <div style={styles.status}>
            Processing audio. Please wait...
          </div>
        )}

        <audio
          ref={audioRef}
          src={currentTrack ? currentTrack.url : ""}
          onEnded={handleNextConvertedTrack}
        />

        {currentTrack && (
          <div style={styles.resultBox}>
            <h2>Converted Output</h2>

            <div style={styles.playerBox}>
              <strong>{currentTrack.originalName}</strong>
              <br />
              <span style={styles.smallText}>
                Instrument: {currentTrack.instrument} | Created:{" "}
                {currentTrack.createdAt}
              </span>
              <br />
              <span style={styles.smallText}>
                Track {currentTrackIndex + 1} of {convertedTracks.length}
              </span>

              <div style={styles.buttonRow}>
                <button style={styles.button} onClick={togglePlayPause}>
                  {isPlaying ? "Pause" : "Play"}
                </button>

                <button style={styles.buttonSecondary} onClick={stopAudio}>
                  Stop
                </button>

                <button
                  style={styles.buttonSecondary}
                  onClick={handlePreviousConvertedTrack}
                >
                  Previous
                </button>

                <button
                  style={styles.buttonSecondary}
                  onClick={handleNextConvertedTrack}
                >
                  Next Song
                </button>
              </div>

              <div style={styles.buttonRow}>
                <a
                  style={styles.download}
                  href={currentTrack.url}
                  download={currentTrack.outputName}
                >
                  Download Output
                </a>
              </div>
            </div>
          </div>
        )}
      </section>
    </main>
  );
}

export default App;