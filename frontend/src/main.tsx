import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MotionConfig } from "motion/react";
import { App } from "./App";
import { spring } from "./motion";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MotionConfig reducedMotion="user" transition={spring}>
      <App />
    </MotionConfig>
  </StrictMode>,
);
