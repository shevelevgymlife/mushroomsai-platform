import React from "react";
import { createRoot } from "react-dom/client";
import { CallRoom } from "./CallRoom";
import "./call.css";

const rootEl = document.getElementById("root");
if (rootEl) {
  createRoot(rootEl).render(
    <React.StrictMode>
      <CallRoom />
    </React.StrictMode>
  );
}
