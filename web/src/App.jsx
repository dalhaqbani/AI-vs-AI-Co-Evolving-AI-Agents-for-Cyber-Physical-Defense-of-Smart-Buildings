import { useEffect, useState } from "react";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

function StatusBadge({ status }) {
  return <span className={`badge ${status}`}>{status}</span>;
}

// Each component type shows its own fields. This is the piece that breaks
// if a 6th component type ever gets added without updating this switch, so
// keep it in sync with backend/main.py's Telemetry union.
function ComponentMetrics({ component }) {
  const { component_type, data } = component;

  if (component_type === "dht22") {
    return (
      <div className="metrics">
        <p><strong>{data.temperature.toFixed(1)}°C</strong><span>Temperature</span></p>
        <p><strong>{data.humidity.toFixed(1)}%</strong><span>Humidity</span></p>
      </div>
    );
  }

  if (component_type === "pir") {
    return (
      <div className="metrics">
        <p><strong>{data.motion ? "Detected" : "Clear"}</strong><span>Motion</span></p>
      </div>
    );
  }

  if (component_type === "servo_lock") {
    return (
      <div className="metrics">
        <p><strong>{data.state === "locked" ? "Locked" : "Unlocked"}</strong><span>Door lock</span></p>
      </div>
    );
  }

  if (component_type === "fan_led") {
    return (
      <div className="metrics">
        <p><strong>{data.state === "on" ? "On" : "Off"}</strong><span>Fan / LED</span></p>
      </div>
    );
  }

  if (component_type === "push_button") {
    return (
      <div className="metrics">
        <p><strong>{data.state === "pressed" ? "Pressed" : "Released"}</strong><span>Access button</span></p>
      </div>
    );
  }

  return <div className="metrics"><p>Unknown component type: {component_type}</p></div>;
}

// The lock gets a real toggle since "lock/unlock" is a meaningful manual
// override. Everything else just gets a generic safe-mode button for now;
// Deem/Mariam's response system will decide the real per-component actions
// in Sprint 3.
function ComponentAction({ component, onAction }) {
  if (component.component_type === "servo_lock") {
    const nextAction = component.data.state === "locked" ? "unlock" : "lock";
    return (
      <button onClick={() => onAction(component.component_id, nextAction)}>
        {nextAction === "lock" ? "Lock door" : "Unlock door"}
      </button>
    );
  }
  return (
    <button onClick={() => onAction(component.component_id, "safe_mode")}>
      Activate Safe Mode
    </button>
  );
}

function ComponentCard({ component, onAction }) {
  return (
    <article className={`card ${component.ai_status}`}>
      <div className="card-title">
        <h2>{component.component_id}</h2>
        <StatusBadge status={component.ai_status} />
      </div>
      <p className="component-type">{component.component_type}</p>
      <ComponentMetrics component={component} />
      {component.is_test && <p className="test-label">Injected teaching event</p>}
      {component.ai_alert && <p className="alert">{component.ai_alert}</p>}
      <ComponentAction component={component} onAction={onAction} />
    </article>
  );
}

export default function App() {
  const [components, setComponents] = useState([]);
  const [error, setError] = useState("");
  const [lastUpdated, setLastUpdated] = useState(null);

  async function loadComponents() {
    try {
      const response = await fetch(`${API_URL}/api/latest`);
      if (!response.ok) throw new Error("Backend unavailable");
      setComponents(await response.json());
      setLastUpdated(new Date());
      setError("");
    } catch (err) {
      setError(err.message);
    }
  }

  async function sendAction(componentId, action) {
    await fetch(`${API_URL}/api/commands`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ component_id: componentId, action }),
    });
  }

  useEffect(() => {
    loadComponents();
    const timer = setInterval(loadComponents, 2000);
    return () => clearInterval(timer);
  }, []);

  return (
    <main>
      <header>
        <div>
          <p className="eyebrow">Sprint 1 — pipeline shell</p>
          <h1>Smart-Building AI Defense</h1>
          <p>Arduino → MQTT → FastAPI → SQLite + placeholder logic → React</p>
        </div>
        <div className="connection">
          <span className={error ? "dot offline" : "dot"}></span>
          {error || "Pipeline online"}
        </div>
      </header>

      <section className="summary">
        <div><strong>{components.length}</strong><span>Active components</span></div>
        <div><strong>{components.filter((c) => c.ai_status !== "normal").length}</strong><span>Current alerts</span></div>
        <div><strong>{lastUpdated ? lastUpdated.toLocaleTimeString() : "—"}</strong><span>Last update</span></div>
      </section>

      <section className="grid">
        {components.map((component) => (
          <ComponentCard key={component.component_id} component={component} onAction={sendAction} />
        ))}
      </section>

      {!components.length && !error && <p className="empty">Waiting for the first MQTT readings…</p>}
    </main>
  );
}
