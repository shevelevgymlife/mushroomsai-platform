import React from "react";
import { createRoot } from "react-dom/client";
import { CallRoom } from "./CallRoom";
import "./call.css";

const rootEl = document.getElementById("root");
if (rootEl) {
  // Без StrictMode: двойной mount в dev рвёт Socket.IO и RTCPeerConnection.
  createRoot(rootEl).render(<CallRoom />);
}
