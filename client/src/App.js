import { useState } from "react";

function App() {
  const [audioFile, setAudioFile] = useState(null);
  const [instrument, setInstrument] = useState("guitar");
  const [outputUrl, setOutputUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const API_URL = "http://localhost:8000/convert";

  const instruments = [
    { value: "guitar", label: "Guitar" },
    { value: "piano", label: "Piano" },
    { value: "flute", label: "Flute" },
    { value: "violin", label: "Violin" },
    { value: "cello", label: "Cello" },
  ];

  const handleFileChange = (event) => {
    const file = event.target.files[0];

    if (!file) return;

    if (!file.type.startsWith("audio/")) {
      setError("Please upload an audio file only.");
      setAudioFile(null);
      return;
    }

    setError("");
    setAudioFile(file);
    setOutputUrl("");
  };

  const handleConvert = async () => {
    if (!audioFile) {
      setError("Please upload an audio file first.");
      return;
    }

    try {
      setLoading(true);
      setError("");
      setOutputUrl("");

      const formData = new FormData();
      formData.append("audio", audioFile);
      formData.append("instrument", instrument);

      const response = await fetch(API_URL, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        throw new Error("Conversion failed. Backend is not ready yet.");
      }

      const audioBlob = await response.blob();
      const url = URL.createObjectURL(audioBlob);
      setOutputUrl(url);
    } catch (err) {
      setError(err.message || "Something went wrong.");
    } finally {
      setLoading(false);
    }
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
      maxWidth: "650px",
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
    },
    button: {
      width: "100%",
      padding: "16px",
      border: "none",
      borderRadius: "14px",
      background: loading ? "#55556e" : "#6c5ce7",
      color: "#ffffff",
      fontSize: "16px",
      fontWeight: "800",
      cursor: loading ? "not-allowed" : "pointer",
    },
    error: {
      marginTop: "18px",
      color: "#ff7675",
      fontWeight: "700",
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
    audio: {
      width: "100%",
      marginBottom: "18px",
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
  };

  return (
    <main style={styles.page}>
      <section style={styles.card}>
        <span style={styles.badge}>AI Music Tool</span>

        <h1 style={styles.title}>TuneMorph AI</h1>

        <p style={styles.subtitle}>
          Same melody, new instrument. Upload a clean melody or cover audio and
          convert it into guitar, piano, flute, violin, or cello.
        </p>

        <div style={styles.formGroup}>
          <label style={styles.label}>Upload Audio</label>
          <input
            style={styles.input}
            type="file"
            accept="audio/*"
            onChange={handleFileChange}
          />
        </div>

        {audioFile && (
          <div style={styles.fileBox}>
            <strong>Selected file:</strong>
            <br />
            {audioFile.name}
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
          style={styles.button}
          onClick={handleConvert}
          disabled={loading}
        >
          {loading ? "Converting..." : "Convert Audio"}
        </button>

        {error && <p style={styles.error}>{error}</p>}

        {loading && (
          <div style={styles.status}>
            Processing audio. Backend ready হলে এখানে converted output আসবে.
          </div>
        )}

        {outputUrl && (
          <div style={styles.resultBox}>
            <h2>Converted Output</h2>

            <audio style={styles.audio} controls src={outputUrl}></audio>

            <a
              style={styles.download}
              href={outputUrl}
              download={`tunemorph-${instrument}.wav`}
            >
              Download Output
            </a>
          </div>
        )}
      </section>
    </main>
  );
}

export default App;