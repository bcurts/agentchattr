import React from "react";
import { createRoot } from "react-dom/client";
import App from "./app/App.tsx";
import "./styles/index.css";

// Catch global script errors
window.addEventListener("error", (event) => {
  document.body.innerHTML += `<div style="color:red;z-index:9999;position:absolute;top:0;left:0;padding:20px;background:#ffebeb;width:100%;">
    <b>Global Error:</b> ${event.message}<br/>
    ${event.filename}:${event.lineno}
  </div>`;
});

// React Error Boundary
class ErrorBoundary extends React.Component<{ children: any }, { hasError: boolean, error: any }> {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error: any) {
    return { hasError: true, error };
  }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{ color: 'red', padding: '20px', background: '#ffebeb' }}>
          <h2>React Render Error</h2>
          <pre>{this.state.error?.toString()}</pre>
          <pre>{this.state.error?.stack}</pre>
        </div>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById("root")!).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>
);