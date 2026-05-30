(function () {
  const sdk = window.__HERMES_PLUGIN_SDK__ || {};
  const React = sdk.React;
  const hooks = sdk.hooks || {};

  if (!React || !window.__HERMES_PLUGINS__) {
    console.error("Ariadne Eval plugin: Hermes plugin SDK unavailable");
    return;
  }

  const h = React.createElement;
  const SESSION_PAGE_SIZE = 24;
  const useEffect = hooks.useEffect || React.useEffect;
  const useMemo = hooks.useMemo || React.useMemo;
  const useState = hooks.useState || React.useState;

  function getHermesSessionToken() {
    try {
      if (typeof sdk.getSessionToken === "function") {
        const token = sdk.getSessionToken();
        if (token) return token;
      }
    } catch (err) {
      // Fall through to other token locations.
    }
    if (sdk.sessionToken) return sdk.sessionToken;
    if (sdk.session_token) return sdk.session_token;
    if (window.__HERMES_SESSION_TOKEN__) return window.__HERMES_SESSION_TOKEN__;
    try {
      return window.localStorage.getItem("hermesSessionToken")
        || window.localStorage.getItem("hermes_session_token")
        || "";
    } catch (err) {
      return "";
    }
  }

  async function fetchPluginJSON(url, options = {}) {
    const headers = { Accept: "application/json" };
    const token = getHermesSessionToken();
    if (token) headers["X-Hermes-Session-Token"] = token;

    const response = await fetch(url, { credentials: "same-origin", headers, signal: options.signal });
    const text = await response.text();
    const contentType = response.headers && response.headers.get ? response.headers.get("content-type") || "" : "";
    let payload = null;

    if (text.trim()) {
      try {
        payload = JSON.parse(text);
      } catch (err) {
        const looksHtml = contentType.includes("text/html") || /^\s*</.test(text);
        const restartHint = "dashboard/plugin API routes may need restart after installing or updating the plugin";
        if (looksHtml) {
          throw new Error(`Ariadne Eval plugin API returned HTML for ${url}; ${restartHint}.`);
        }
        throw new Error(`Ariadne Eval plugin API returned non-JSON for ${url}; ${restartHint}.`);
      }
    }

    if (!response.ok) {
      const message = payload && (payload.detail || payload.message || payload.error);
      throw new Error(message || `Ariadne Eval plugin API request failed with HTTP ${response.status}`);
    }
    return payload;
  }

  async function sendPluginJSON(url, method, payload) {
    const headers = { Accept: "application/json", "Content-Type": "application/json" };
    const token = getHermesSessionToken();
    if (token) headers["X-Hermes-Session-Token"] = token;

    const response = await fetch(url, {
      method,
      credentials: "same-origin",
      headers,
      body: JSON.stringify(payload || {}),
    });
    const text = await response.text();
    const data = text.trim() ? JSON.parse(text) : null;
    if (!response.ok) {
      const message = data && (data.detail || data.message || data.error);
      throw new Error(message || `Ariadne Eval plugin API request failed with HTTP ${response.status}`);
    }
    return data;
  }

  async function postPluginJSON(url, payload) {
    return sendPluginJSON(url, "POST", payload);
  }

  async function patchPluginJSON(url, payload) {
    return sendPluginJSON(url, "PATCH", payload);
  }

  function formatCount(value) {
    return Number(value || 0).toLocaleString();
  }

  function timestampToDate(value) {
    if (value === null || value === undefined || value === "") return null;
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return null;
    return new Date(numeric > 1000000000000 ? numeric : numeric * 1000);
  }

  function formatTimestampAttr(value) {
    const date = timestampToDate(value);
    return date ? date.toISOString() : "";
  }

  function formatLocalTime(value) {
    const date = timestampToDate(value);
    if (!date) return "unknown time";
    return date.toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  }

  function LocalTime({ value }) {
    return h("time", {
      className: "ae-local-time",
      dateTime: formatTimestampAttr(value),
      title: formatTimestampAttr(value),
    }, formatLocalTime(value));
  }

  function clip(text, limit) {
    const value = String(text || "").replace(/\s+/g, " ").trim();
    if (!value) return "—";
    return value.length > limit ? `${value.slice(0, limit - 1)}…` : value;
  }

  function displayLabel(value) {
    const text = String(value || "").replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
    if (!text) return "unknown";
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  const FEEDBACK_LABELS = {
    problem: "Problem",
    ok: "OK",
    unsure: "Unsure",
  };

  function formatScore(value) {
    const numeric = Number(value || 0);
    if (!Number.isFinite(numeric)) return "0.00";
    return numeric.toFixed(2);
  }

  function taskFormDefaults(task) {
    const source = task || {};
    return {
      id: source.id || "",
      name: source.name || "",
      enabled: Boolean(source.enabled),
      schedule_kind: source.schedule_kind || "interval",
      interval_hours: Math.max(1, Math.round(Number(source.interval_seconds || 18000) / 3600)),
      no_gap: Boolean(source.no_gap),
      idle_backoff_seconds: Number(source.idle_backoff_seconds || 300),
      candidate_limit: Number(source.candidate_limit || 10),
      min_priority_score: Number(source.min_priority_score || 1),
      cooldown_minutes: Number(source.cooldown_minutes || 120),
      judgement_threshold: source.judgement_threshold || "strict",
      max_judge_calls: Number(source.max_judge_calls || 5),
      max_review_total_tokens: source.max_review_total_tokens === null || source.max_review_total_tokens === undefined ? "" : Number(source.max_review_total_tokens),
      max_tokens_per_call: Number(source.max_tokens_per_call || 1200),
    };
  }

  function taskPayloadFromForm(form) {
    return {
      name: String(form.name || "").trim(),
      enabled: Boolean(form.enabled),
      schedule_kind: form.schedule_kind,
      interval_seconds: Math.max(1, Number(form.interval_hours || 1)) * 3600,
      no_gap: Boolean(form.no_gap),
      idle_backoff_seconds: Math.max(1, Number(form.idle_backoff_seconds || 1)),
      candidate_limit: Math.max(1, Number(form.candidate_limit || 1)),
      min_priority_score: Math.max(0, Number(form.min_priority_score || 0)),
      cooldown_minutes: Math.max(0, Number(form.cooldown_minutes || 0)),
      judgement_threshold: form.judgement_threshold,
      max_judge_calls: Math.max(0, Number(form.max_judge_calls || 0)),
      max_review_total_tokens: form.max_review_total_tokens === "" ? null : Math.max(0, Number(form.max_review_total_tokens || 0)),
      max_tokens_per_call: Math.max(1, Number(form.max_tokens_per_call || 1)),
    };
  }

  function Field({ label, children }) {
    return h("label", { className: "ae-config-field" },
      h("span", null, label),
      children
    );
  }

  function countByLabel(rows, keyFn) {
    const counts = {};
    (rows || []).forEach((row) => {
      const key = keyFn(row) || "unknown";
      counts[key] = Number(counts[key] || 0) + 1;
    });
    return counts;
  }

  function copyText(text) {
    const value = String(text || "");
    if (!value) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).catch(() => {});
      return;
    }
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();
    try {
      document.execCommand("copy");
    } catch (err) {
      // Best effort fallback only.
    }
    document.body.removeChild(textarea);
  }

  function CopyButton({ value, label, title }) {
    return h("button", {
      type: "button",
      className: "ae-copy-button",
      title: title || "Copy text",
      onClick: (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        copyText(value);
      },
    }, label || "Copy");
  }

  function ExpandedText({ value, summaryLimit = 220 }) {
    const full = String(value || "").trim();
    if (!full) return h("p", { className: "ae-muted" }, "—");
    if (full.length <= summaryLimit) {
      return h("p", { className: "ae-full-inline" }, full);
    }
    return h("details", { className: "ae-expanded-text" },
      h("summary", null, clip(full, summaryLimit)),
      h("pre", { className: "ae-full-text" }, full),
      h(CopyButton, { value: full, label: "Copy detail", title: "Copy full detail" })
    );
  }

  function StatCard({ label, value, sub }) {
    return h("div", { className: "ae-stat-card" },
      h("div", { className: "ae-stat-label" }, label),
      h("div", { className: "ae-stat-value" }, formatCount(value)),
      sub ? h("div", { className: "ae-stat-sub" }, sub) : null
    );
  }

  function EvalTaskControls({ tasks, onAction }) {
    if (!tasks || !tasks.length) {
      return h("div", { className: "ae-empty" }, "No recurring review jobs configured.");
    }
    return h("div", { className: "ae-task-list" }, tasks.slice(0, 6).map((task) =>
      h("div", { className: "ae-task-row", key: task.id },
        h("div", null,
          h("strong", null, task.name || task.id),
          h("div", { className: "ae-muted" }, `${task.schedule_kind} · next ${formatLocalTime(task.next_due_at)} · v${task.config_version}`)
        ),
        h("div", { className: "ae-task-actions" },
          h("button", { type: "button", onClick: () => onAction(task.id, "run-now") }, "Run now"),
          h("button", { type: "button", onClick: () => onAction(task.id, task.enabled ? "pause" : "resume") }, task.enabled ? "Pause" : "Resume")
        )
      )
    ));
  }

  function ConfigPanel({ config, tasks, loading, error, onClose, onRefresh, onTaskSaved, onTaskAction, onError }) {
    const models = (config && config.problem_reviewer_models) || [];
    const promoted = config && config.promoted_problem_reviewer_model;
    const options = (config && config.review_job_options) || {};
    const [selectedTaskId, setSelectedTaskId] = useState("");
    const [form, setForm] = useState(taskFormDefaults(null));
    const [selectedModelId, setSelectedModelId] = useState(promoted && promoted.id || "");
    const [saving, setSaving] = useState(false);
    const [modelBusy, setModelBusy] = useState(false);

    useEffect(() => {
      const selected = (tasks || []).find((task) => task.id === selectedTaskId);
      setForm(taskFormDefaults(selected || null));
    }, [selectedTaskId, tasks]);

    useEffect(() => {
      if (promoted && promoted.id) setSelectedModelId(promoted.id);
      else if (models[0] && models[0].id) setSelectedModelId(models[0].id);
    }, [config]);

    function updateForm(key, value) {
      setForm((current) => Object.assign({}, current, { [key]: value }));
    }

    function saveTask(ev) {
      ev.preventDefault();
      const payload = taskPayloadFromForm(form);
      if (!payload.name) {
        onError("Task name is required.");
        return;
      }
      setSaving(true);
      const request = selectedTaskId
        ? patchPluginJSON(`/api/plugins/ariadne-eval/review-jobs/${encodeURIComponent(selectedTaskId)}`, payload)
        : postPluginJSON("/api/plugins/ariadne-eval/review-jobs", payload);
      request
        .then((task) => {
          setSelectedTaskId(task && task.id || selectedTaskId);
          onTaskSaved();
        })
        .catch((err) => onError(err && err.message ? err.message : String(err)))
        .finally(() => setSaving(false));
    }

    function promoteModel() {
      if (!selectedModelId) {
        onError("Choose a trained problem model to promote.");
        return;
      }
      setModelBusy(true);
      postPluginJSON(`/api/plugins/ariadne-eval/tool-outcome-reviewer-models/${encodeURIComponent(selectedModelId)}/promote`, {})
        .then(onRefresh)
        .catch((err) => onError(err && err.message ? err.message : String(err)))
        .finally(() => setModelBusy(false));
    }

    function retrainModel() {
      setModelBusy(true);
      postPluginJSON("/api/plugins/ariadne-eval/tool-outcome-reviewer-models/retrain", {})
        .then(onRefresh)
        .catch((err) => onError(err && err.message ? err.message : String(err)))
        .finally(() => setModelBusy(false));
    }

    return h("section", { className: "ae-config-panel" },
      h("div", { className: "ae-config-head" },
        h("div", null,
          h("h2", null, "Configuration"),
          h("p", { className: "ae-muted" }, "Browsing this panel is read-only. Save, Promote, Retrain, Run now, Pause, and Resume are the only write actions.")
        ),
        h("div", { className: "ae-config-actions" },
          h("button", { type: "button", onClick: onRefresh, disabled: loading }, loading ? "Loading…" : "Refresh"),
          h("button", { type: "button", onClick: onClose }, "Close")
        )
      ),
      error ? h("div", { className: "ae-error" }, error) : null,
      h("div", { className: "ae-config-grid" },
        h("div", { className: "ae-config-card" },
          h("h3", null, "Problem ML model"),
          models.length ? h(Field, { label: "Trained model" },
            h("select", { value: selectedModelId, onChange: (ev) => setSelectedModelId(ev.target.value) },
              models.map((model) => h("option", { key: model.id, value: model.id },
                `${model.model_name} ${model.model_version}${model.promoted ? " (promoted)" : ""}`
              ))
            )
          ) : h("div", { className: "ae-empty" }, "No trained problem models recorded."),
          promoted ? h("p", { className: "ae-muted" }, `Current model: ${promoted.model_name} ${promoted.model_version}, ${formatCount(promoted.training_record_count)} training rows.`) : h("p", { className: "ae-muted" }, "No model is promoted yet."),
          h("div", { className: "ae-config-actions" },
            h("button", { type: "button", onClick: promoteModel, disabled: modelBusy || !models.length }, "Promote"),
            h("button", { type: "button", onClick: retrainModel, disabled: modelBusy }, modelBusy ? "Working…" : "Retrain")
          )
        ),
        h("div", { className: "ae-config-card" },
          h("h3", null, "LLM judging route"),
          h("p", { className: "ae-muted" }, (config && config.llm_judging && config.llm_judging.route_priority) || "Hermes route priority is used for judging."),
          h("p", { className: "ae-muted" }, "Provider authentication and arbitrary model routing stay in Hermes.")
        ),
        h("form", { className: "ae-config-card ae-config-task", onSubmit: saveTask },
          h("div", { className: "ae-panel-title-row" },
            h("h3", null, "Recurring review job"),
            h("button", { type: "button", onClick: () => { setSelectedTaskId(""); setForm(taskFormDefaults(null)); } }, "New task")
          ),
          h(Field, { label: "Select task" },
            h("select", { value: selectedTaskId, onChange: (ev) => setSelectedTaskId(ev.target.value) },
              h("option", { value: "" }, "Create new task"),
              (tasks || []).map((task) => h("option", { key: task.id, value: task.id }, task.name || task.id))
            )
          ),
          h("div", { className: "ae-config-form-grid" },
            h(Field, { label: "Name" }, h("input", { value: form.name, onChange: (ev) => updateForm("name", ev.target.value) })),
            h(Field, { label: "Schedule" }, h("select", { value: form.schedule_kind, onChange: (ev) => updateForm("schedule_kind", ev.target.value) },
              (options.schedule_kinds || ["interval", "continuous"]).map((kind) => h("option", { key: kind, value: kind }, displayLabel(kind)))
            )),
            h(Field, { label: "Interval hours" }, h("input", { type: "number", min: "1", value: form.interval_hours, onChange: (ev) => updateForm("interval_hours", ev.target.value) })),
            h(Field, { label: "Idle backoff seconds" }, h("input", { type: "number", min: "1", value: form.idle_backoff_seconds, onChange: (ev) => updateForm("idle_backoff_seconds", ev.target.value) })),
            h(Field, { label: "Candidate limit" }, h("input", { type: "number", min: "1", value: form.candidate_limit, onChange: (ev) => updateForm("candidate_limit", ev.target.value) })),
            h(Field, { label: "Min priority score" }, h("input", { type: "number", min: "0", value: form.min_priority_score, onChange: (ev) => updateForm("min_priority_score", ev.target.value) })),
            h(Field, { label: "Cooldown minutes" }, h("input", { type: "number", min: "0", value: form.cooldown_minutes, onChange: (ev) => updateForm("cooldown_minutes", ev.target.value) })),
            h(Field, { label: "Judgement threshold" }, h("select", { value: form.judgement_threshold, onChange: (ev) => updateForm("judgement_threshold", ev.target.value) },
              (options.judgement_thresholds || ["strict", "balanced", "relaxed"]).map((threshold) => h("option", { key: threshold, value: threshold }, displayLabel(threshold)))
            )),
            h(Field, { label: "Max judge calls" }, h("input", { type: "number", min: "0", value: form.max_judge_calls, onChange: (ev) => updateForm("max_judge_calls", ev.target.value) })),
            h(Field, { label: "Max judge total tokens" }, h("input", { type: "number", min: "0", placeholder: "No cap", value: form.max_review_total_tokens, onChange: (ev) => updateForm("max_review_total_tokens", ev.target.value) })),
            h(Field, { label: "Max tokens per call" }, h("input", { type: "number", min: "1", value: form.max_tokens_per_call, onChange: (ev) => updateForm("max_tokens_per_call", ev.target.value) }))
          ),
          h("div", { className: "ae-config-checks" },
            h("label", null, h("input", { type: "checkbox", checked: form.enabled, onChange: (ev) => updateForm("enabled", ev.target.checked) }), " Enabled"),
            h("label", null, h("input", { type: "checkbox", checked: form.no_gap, onChange: (ev) => updateForm("no_gap", ev.target.checked) }), " No gap")
          ),
          h("div", { className: "ae-config-actions" },
            h("button", { type: "submit", disabled: saving }, saving ? "Saving…" : (selectedTaskId ? "Save task" : "Create task")),
            selectedTaskId ? h("button", { type: "button", onClick: () => onTaskAction(selectedTaskId, "run-now") }, "Run now") : null,
            selectedTaskId ? h("button", { type: "button", onClick: () => onTaskAction(selectedTaskId, form.enabled ? "pause" : "resume") }, form.enabled ? "Pause" : "Resume") : null
          )
        )
      )
    );
  }

  function SeverityTag({ severity }) {
    const label = severity || "medium";
    return h("span", { className: `ae-severity ae-severity-${label}`, title: label }, displayLabel(label));
  }

  function CountMap({ values, empty }) {
    const rows = Object.entries(values || {}).sort((a, b) => Number(b[1]) - Number(a[1]) || a[0].localeCompare(b[0]));
    if (!rows.length) return h("span", { className: "ae-muted" }, empty || "none");
    return h("div", { className: "ae-count-map" }, rows.slice(0, 6).map(([key, count]) =>
      h("span", { className: "ae-count-pill", key, title: key }, h("b", null, displayLabel(key)), " ", formatCount(count))
    ));
  }

  function ChipList({ rows, labelKey }) {
    if (!rows || !rows.length) return h("div", { className: "ae-empty" }, "No data in this window.");
    return h("div", { className: "ae-chip-list" }, rows.slice(0, 8).map((row) =>
      h("div", { className: "ae-chip", key: row[labelKey] },
        h("span", { title: row[labelKey] }, displayLabel(row[labelKey])),
        h("strong", null, formatCount(row.count))
      )
    ));
  }

  function ToolOutcomeReviewChips({ rows }) {
    return h(CountMap, {
      values: countByLabel(rows, (row) => row.label || row.prediction_label),
      empty: "none",
    });
  }

  function StatusBars({ statuses }) {
    const entries = Object.entries(statuses || {});
    const total = entries.reduce((sum, [, count]) => sum + Number(count || 0), 0) || 1;
    if (!entries.length) return h("div", { className: "ae-empty" }, "No judged turns yet.");
    return h("div", { className: "ae-bars" }, entries.map(([status, count]) =>
      h("div", { className: "ae-bar-row", key: status },
        h("div", { className: "ae-bar-meta" }, h("span", { title: status }, displayLabel(status)), h("strong", null, formatCount(count))),
        h("div", { className: "ae-bar-track" }, h("div", { className: `ae-bar-fill ae-${status}`, style: { width: `${Math.max(4, (Number(count) / total) * 100)}%` } }))
      )
    ));
  }

  function Timeline({ timeline }) {
    const rows = timeline || [];
    const peak = rows.reduce((max, row) => Math.max(max, Number(row.findings || 0)), 1);
    if (!rows.length) return h("div", { className: "ae-empty" }, "No timeline data in this window.");
    return h("div", { className: "ae-timeline" }, rows.slice(-36).map((row) => {
      const total = Number(row.findings || 0);
      const height = Math.max(8, (total / peak) * 84);
      const label = formatLocalTime(row.bucket_start);
      return h("div", { className: "ae-timeline-col", key: row.bucket_start, title: `${label}: ${formatCount(row.findings)} findings` },
        h("div", { className: "ae-timeline-bar", style: { height: `${height}px` } },
          h("span", { className: "ae-timeline-findings", style: { height: `${total ? (Number(row.findings || 0) / total) * 100 : 0}%` } })
        )
      );
    }));
  }

  function safePrettyJson(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "object") return JSON.stringify(value, null, 2);
    const text = String(value);
    try {
      return JSON.stringify(JSON.parse(text), null, 2);
    } catch (err) {
      return text;
    }
  }

  function SafeJsonBlock({ value, title }) {
    const text = safePrettyJson(value);
    return h("div", { className: "ae-json-block" },
      h("div", { className: "ae-block-head" },
        h("strong", null, title || "JSON"),
        h(CopyButton, { value: text, label: "Copy", title: `Copy ${title || "JSON"}` })
      ),
      h("pre", { className: "ae-pre" }, text)
    );
  }

  function RawJsonBlock({ value, title }) {
    return h(SafeJsonBlock, { value, title });
  }

  function FeedbackButtons({ targetType, targetId, evalUnitId, currentLabel, labels, commentPrefix, reasonCode }) {
    const [status, setStatus] = useState("");
    const options = labels || ["problem", "ok", "unsure"];
    if (!targetType || !targetId) return null;

    function submitFeedback(label) {
      setStatus("saving");
      postPluginJSON(`/api/plugins/ariadne-eval/feedback`, {
        target_type: targetType,
        target_id: targetId,
        turn_case_id: evalUnitId,
        label,
        reason_code: reasonCode,
        correction: label !== currentLabel,
        comment: `${commentPrefix || "Marked from dashboard"} as ${label}.`,
        reviewer: "dashboard",
      })
        .then(() => setStatus(`saved: ${displayLabel(label)}`))
        .catch((err) => setStatus(err && err.message ? err.message : String(err)));
    }

    return h("div", { className: "ae-review-actions", onClick: (ev) => ev.stopPropagation(), "aria-label": "Feedback controls" },
      h("span", { className: "ae-review-label" }, "Feedback"),
      options.map((label) => h("button", { key: label, type: "button", onClick: () => submitFeedback(label) }, FEEDBACK_LABELS[label] || displayLabel(label))),
      status ? h("small", { className: status.startsWith("saved") ? "ae-review-success" : "ae-review-status" }, status) : null
    );
  }

  function ReviewButtons({ row }) {
    if (!row || !row.id) return null;
    return h(FeedbackButtons, {
      targetType: "tool_outcome_case",
      targetId: row.id,
      evalUnitId: row.turn_case_id,
      currentLabel: row.label || row.prediction_label,
      labels: ["problem", "ok", "unsure"],
      commentPrefix: "Marked problem evidence from dashboard",
      reasonCode: row.reason_code,
    });
  }

  function FrictionSummary({ friction, anchors }) {
    const values = friction || {};
    const hasData = values.count || values.max_friction_score || values.avg_friction_score;
    return h("section", { className: "ae-panel ae-wide ae-friction-panel" },
      h("div", { className: "ae-panel-title-row" },
        h("h2", null, "Request friction"),
        h("span", { className: "ae-muted" }, hasData ? "Normalized 0.00 to 1.00" : "No judged request friction yet")
      ),
      h("div", { className: "ae-friction-stats" },
        h("div", null, h("label", null, "Average"), h("strong", null, formatScore(values.avg_friction_score))),
        h("div", null, h("label", null, "Max"), h("strong", null, formatScore(values.max_friction_score))),
        h("div", null, h("label", null, "Count"), h("strong", null, formatCount(values.count)))
      ),
      h("div", { className: "ae-anchor-legend" }, (anchors || []).map((anchor) =>
        h("span", { className: `ae-anchor ae-anchor-${anchor.label}`, key: anchor.label, title: anchor.description },
          h("b", null, formatScore(anchor.score)), " ", displayLabel(anchor.label)
        )
      ))
    );
  }

  function RequestCard({ request, onOpenSession }) {
    function openDetails(ev) {
      ev.preventDefault();
      ev.stopPropagation();
      if (onOpenSession) onOpenSession(request.source_session_id, { unitId: request.turn_case_id, tab: "turns" });
    }
    return h("div", { className: "ae-request-card" },
      h("div", { className: "ae-request-head" },
        h("strong", null, clip(request.request_text, 220)),
        h("span", { className: `ae-friction-band ae-band-${request.friction_band || "clean"}` }, `${formatScore(request.friction_score)} ${displayLabel(request.friction_band)}`)
      ),
      h("div", { className: "ae-evidence-meta" },
        h("span", { className: "ae-copyable-id", title: request.source_session_id }, `session ${request.source_session_id || "unknown"}`),
        h("span", null, `status ${displayLabel(request.outcome_status)}`),
        h("span", null, `${formatCount(request.finding_count)} findings`),
        h("span", null, `${formatCount(request.tool_outcome_case_count)} tool outcome cases`),
        request.started_at ? h(LocalTime, { value: request.started_at }) : null
      ),
      request.summary_reason ? h("p", null, clip(request.summary_reason, 220)) : null,
      h(FeedbackButtons, {
        targetType: "turn_case",
        targetId: request.turn_case_id,
        evalUnitId: request.turn_case_id,
        currentLabel: request.outcome_status,
        labels: ["succeed", "failed", "mishandled", "prolonged"],
        commentPrefix: "Marked request from dashboard",
      }),
      onOpenSession ? h("button", { type: "button", className: "ae-details-button", onClick: openDetails }, "Details") : null
    );
  }

  function RequestsNeedingAttention({ requests, onOpenSession }) {
    const rows = (requests || []).slice().sort((a, b) =>
      Number(b.friction_score || 0) - Number(a.friction_score || 0)
      || Number(b.finding_count || 0) - Number(a.finding_count || 0)
      || Number(b.tool_outcome_case_count || 0) - Number(a.tool_outcome_case_count || 0)
    );
    if (!rows.length) return h("div", { className: "ae-empty" }, "No judged requests in this window.");
    return h("div", { className: "ae-request-list" }, rows.slice(0, 12).map((request) =>
      h(RequestCard, { key: request.turn_case_id, request, onOpenSession })
    ));
  }

  function openRowFromEvidence(openSession, sessionId, row, type) {
    if (!openSession) return;
    const hasEvent = Boolean(row && row.related_event_id);
    const tab = hasEvent ? "tools" : (type === "findings" || row.finding_type ? "judge" : "tools");
    openSession(sessionId, {
      unitId: row && row.turn_case_id,
      eventId: row && row.related_event_id,
      tab,
    });
  }

  function EvidenceList({ rows, type, sourceSessionId, onOpenSession }) {
    if (!rows || !rows.length) return h("div", { className: "ae-empty ae-compact-empty" }, `No ${type}.`);
    return h("div", { className: "ae-evidence-list" }, rows.slice(0, 8).map((row, idx) => {
      const name = row.finding_type || row.label || row.prediction_label || "unknown";
      const detailText = row.tool_result || row.output_preview || row.evidence || row.summary_reason || row.request_text_excerpt || row.request_text;
      const sessionId = row.source_session_id || sourceSessionId;
      const key = `${row.turn_case_id}:${name}:${idx}`;
      const onActivate = (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        openRowFromEvidence(onOpenSession, sessionId, row, type);
      };
      return h("div", { className: "ae-evidence ae-evidence-row",
        key,
        role: "button",
        tabIndex: 0,
        title: "Open detail drawer for this evidence",
        onClick: onActivate,
        onKeyDown: (ev) => {
          if (ev.key === "Enter" || ev.key === " ") onActivate(ev);
        },
      },
        h("div", { className: "ae-evidence-head" },
          h("strong", { title: name }, displayLabel(name)),
          h(SeverityTag, { severity: row.severity })
        ),
        h("div", { className: "ae-evidence-meta" },
          h(LocalTime, { value: row.started_at || row.result_timestamp }),
          h("span", null, `turn ${row.turn_index || "?"}`),
          h("span", { className: "ae-copyable-id", title: row.turn_case_id || "" }, `unit ${row.turn_case_id || "?"}`),
          row.related_event_id ? h("span", { className: "ae-copyable-id", title: row.related_event_id }, "linked event") : null
        ),
        h(ExpandedText, { value: detailText, summaryLimit: 260 }),
        h(ReviewButtons, { row }),
        row.label_source ? h("small", null, `label source: ${row.label_source}`) : null,
        row.evidence && row.output_preview ? h("small", null, row.evidence) : null,
        h("small", null, clip(row.request_text_excerpt || row.request_text, 180))
      );
    }));
  }

  function SessionCard({ row }) {
    const [collapsed, setCollapsed] = useState(false);

    function toggleCollapsed() {
      const selection = window.getSelection && window.getSelection();
      if (selection && String(selection).trim()) return;
      setCollapsed((value) => !value);
    }

    function openDetails(ev) {
      ev.preventDefault();
      ev.stopPropagation();
      if (row.onOpenSession) row.onOpenSession(row.source_session_id, { tab: "overview" });
    }

    return h("div", {
      className: `ae-session-card ${collapsed ? "ae-collapsed" : ""}`,
    },
      h("div", {
        className: "ae-session-collapse-zone",
        "aria-expanded": !collapsed,
        onClick: toggleCollapsed,
        onKeyDown: (ev) => {
          if (ev.target !== ev.currentTarget) return;
          if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            toggleCollapsed();
          }
        },
        role: "button",
        tabIndex: 0,
        title: collapsed ? "Expand session" : "Collapse session",
      },
        h("div", { className: "ae-session-card-head" },
          h("div", { className: "ae-session" },
            h("strong", null, row.title || row.source_session_id),
            h(LocalTime, { value: row.last_started_at }),
            h("span", { className: "ae-copyable-id", title: row.source_session_id }, row.source_session_id)
          ),
          h("div", { className: "ae-session-head-actions" },
            h("button", {
              type: "button",
              className: "ae-details-button",
              title: "Open session detail drawer",
              onClick: openDetails,
            }, "Details"),
            h("button", {
              type: "button",
              className: "ae-copy-button",
              title: "Copy session ID",
              onClick: (ev) => {
                ev.preventDefault();
                ev.stopPropagation();
                copyText(row.source_session_id);
              },
            }, "Copy ID"),
            h("span", { className: "ae-card-fold" }, collapsed ? "Expand" : "Fold"),
            h("div", { className: "ae-session-attention", title: "Session evidence count" }, `${formatCount(Number(row.tool_outcome_case_count || 0) + Number(row.finding_count || 0))} signals`)
          )
        ),
        h("div", { className: "ae-session-metrics" },
          h("span", null, h("b", null, formatCount(row.turn_cases)), " cases"),
          h("span", null, h("b", null, formatScore(row.max_friction_score)), " max friction"),
          h("span", null, h("b", null, formatScore(row.avg_friction_score)), " avg friction"),
          h("span", null, h("b", null, formatCount(row.tool_outcome_case_count)), " tool outcome cases"),
          h("span", null, h("b", null, formatCount(row.finding_count)), " findings")
        )
      ),
      !collapsed ? h("div", { className: "ae-session-card-body" },
        h("div", { className: "ae-session-facets" },
          h("div", null, h("label", null, "Findings"), h(CountMap, { values: row.finding_types, empty: "none" })),
          h("div", null, h("label", null, "Tool outcome cases"), h(ToolOutcomeReviewChips, { rows: row.tool_outcome_cases }))
        ),
        h("div", { className: "ae-evidence-sections" },
          h("div", { className: "ae-evidence-section" },
            h("label", null, "Finding evidence"),
            h(EvidenceList, { rows: row.findings, type: "findings", sourceSessionId: row.source_session_id, onOpenSession: row.onOpenSession })
          ),
          h("div", { className: "ae-evidence-section" },
            h("label", null, "Problem evidence"),
            h(EvidenceList, { rows: row.tool_outcome_cases, type: "tool_outcome_cases", sourceSessionId: row.source_session_id, onOpenSession: row.onOpenSession })
          )
        )
      ) : null
    );
  }

  function SessionGroups({ sessions, onOpenSession }) {
    if (!sessions || !sessions.length) return h("div", { className: "ae-empty" }, "No sessions in this window.");
    return h("div", { className: "ae-session-grid" }, sessions.map((row) =>
      h(SessionCard, {
        key: row.source_session_id,
        row: Object.assign({}, row, { onOpenSession }),
      })
    ));
  }

  function SessionPaginationControls({ pagination, onPage }) {
    const page = pagination || {};
    const limit = Math.max(1, Number(page.limit || SESSION_PAGE_SIZE));
    const offset = Math.max(0, Number(page.offset || 0));
    const total = Math.max(0, Number(page.total || 0));
    const pageStart = total ? offset + 1 : 0;
    const pageEnd = Math.min(total, offset + limit);
    const previousOffset = Math.max(0, offset - limit);
    const nextOffset = offset + limit;
    return h("div", { className: "ae-pagination", role: "navigation", "aria-label": "Agent sessions pages" },
      h("button", {
        type: "button",
        disabled: !page.has_prev,
        onClick: () => onPage(previousOffset),
      }, "Previous"),
      h("span", { className: "ae-muted" }, `${formatCount(pageStart)}–${formatCount(pageEnd)} of ${formatCount(total)} sessions`),
      h("button", {
        type: "button",
        disabled: !page.has_next,
        onClick: () => onPage(nextOffset),
      }, "Next")
    );
  }

  function DrawerTabs({ activeTab, setActiveTab }) {
    const tabs = [
      ["overview", "Overview"],
      ["turns", "Turns"],
      ["tools", "Tool calls"],
      ["judge", "Judge eval"],
      ["raw", "Raw"],
    ];
    return h("div", { className: "ae-drawer-tabs", role: "tablist" }, tabs.map(([key, label]) =>
      h("button", {
        key,
        type: "button",
        className: activeTab === key ? "ae-active-tab" : "",
        onClick: () => setActiveTab(key),
        role: "tab",
        "aria-selected": activeTab === key,
      }, label)
    ));
  }

  function selectedDetail(drawerData, selectedUnitId) {
    const rows = (drawerData && drawerData.cases) || [];
    return rows.find((detail) => detail.unit && detail.unit.id === selectedUnitId) || rows[0] || null;
  }

  function SessionOverviewTab({ data, onJump }) {
    const cases = data.cases || [];
    const latest = cases[0] && cases[0].unit;
    return h("div", { className: "ae-section" },
      h("h3", null, "Why this session is hot"),
        h("div", { className: "ae-drawer-summary-grid" },
        h("div", null, h("label", null, "Statuses"), h(CountMap, { values: data.statuses, empty: "none" })),
        h("div", null, h("label", null, "Findings"), h(CountMap, { values: data.finding_types, empty: "none" })),
        h("div", null, h("label", null, "Tool outcome cases"), h(ToolOutcomeReviewChips, { rows: data.tool_outcome_cases })),
        h("div", null, h("label", null, "Severities"), h(CountMap, { values: data.severities, empty: "none" }))
      ),
      h("div", { className: "ae-drawer-actions" },
        h(CopyButton, { value: data.source_session_id, label: "Copy session ID", title: "Copy session ID" }),
        h(CopyButton, { value: JSON.stringify(data, null, 2), label: "Copy overview JSON", title: "Copy session detail JSON" }),
        latest ? h("button", { type: "button", onClick: () => onJump(latest.id, null, "turns") }, "Open latest unit") : null
      ),
      h("h4", null, "Top unit evidence"),
      h("div", { className: "ae-unit-list" }, cases.slice(0, 8).map((detail) => {
        const row = detail.unit || {};
        const evalRow = detail.latest_eval || {};
        const firstEvent = (detail.case_events || []).find((event) => event.output_error) || (detail.case_events || [])[0];
        return h("button", {
          type: "button",
          className: "ae-turn-row",
          key: row.id,
          onClick: () => onJump(row.id, firstEvent && firstEvent.id, firstEvent ? "tools" : "judge"),
        },
          h("strong", null, `Turn ${row.turn_index || "?"} · ${evalRow.outcome_status || "not judged"}`),
          h("span", null, clip(evalRow.summary_reason || row.request_text, 180))
        );
      }))
    );
  }

  function TurnDetail({ detail }) {
    const row = (detail && detail.unit) || {};
    const evalRow = (detail && detail.latest_eval) || {};
    return h("div", { className: "ae-turn-detail" },
      h("div", { className: "ae-drawer-summary-grid" },
        h("div", null, h("label", null, "Signals"), h(CountMap, { values: Object.fromEntries((detail.signals || []).map((signal) => [signal.signal_type, 1])), empty: "none" })),
        h("div", null, h("label", null, "Tool errors"), h(CountMap, { values: Object.fromEntries((detail.case_events || []).filter((event) => event.output_error).map((event) => [event.tool_name || "unknown", 1])), empty: "none" }))
      ),
      h("h4", null, "User request"), h("pre", { className: "ae-pre" }, row.request_text || "—"),
      h("h4", null, "Assistant response"), h("pre", { className: "ae-pre" }, row.response_text || "—"),
      h("h4", null, "Previous context"), h("pre", { className: "ae-pre" }, row.prior_context_summary || "—"),
      h("h4", null, "Next user reaction"), h("pre", { className: "ae-pre" }, row.next_request_text || "—"),
      h("h4", null, "Latest judge"), h("pre", { className: "ae-pre" }, `${evalRow.outcome_status || "not judged"}: ${evalRow.summary_reason || "—"}`),
      h(FeedbackButtons, {
        targetType: "turn_case",
        targetId: row.id,
        evalUnitId: row.id,
        currentLabel: evalRow.outcome_status,
        labels: ["succeed", "failed", "mishandled", "prolonged"],
        commentPrefix: "Marked request turn from dashboard",
      })
    );
  }

  function SessionTurnsTab({ data, selectedUnitId, setSelectedUnitId }) {
    const rows = data.cases || [];
    return h("div", { className: "ae-section" }, rows.map((detail) => {
      const row = detail.unit || {};
      const evalRow = detail.latest_eval || {};
      const open = row.id === selectedUnitId;
      return h("div", { className: `ae-turn-row ${open ? "ae-turn-selected" : ""}`, key: row.id },
        h("button", { type: "button", onClick: () => setSelectedUnitId(row.id) },
          h("strong", null, `Turn ${row.turn_index || "?"} · ${evalRow.outcome_status || "not judged"} · ${evalRow.confidence || "unknown"}`),
          h("span", null, h(LocalTime, { value: row.started_at }), " · ", formatCount(row.tool_interaction_count), " tools · ", formatCount(row.source_session_api_interaction_count), " APIs")
        ),
        h("p", null, clip(row.request_text, 240)),
        open ? h(TurnDetail, { detail }) : null
      );
    }));
  }

  function relatedEventIds(detail) {
    const ids = new Set();
    const evalRow = detail.latest_eval || {};
    (evalRow.findings || []).forEach((finding) => { if (finding.related_event_id) ids.add(finding.related_event_id); });
    return ids;
  }

  function TraceEventRow({ event, detail, selectedEventId }) {
    const linked = relatedEventIds(detail).has(event.id);
    const defaultOpen = Boolean(event.output_error) || event.id === selectedEventId;
    return h("details", {
      className: `ae-trace-event ${event.output_error ? "ae-trace-event-error" : ""} ${linked ? "ae-trace-event-linked" : ""} ${event.id === selectedEventId ? "ae-trace-event-selected" : ""}`,
      id: `ae-event-${String(event.id || "").replace(/[^a-zA-Z0-9_-]/g, "-")}`,
      open: defaultOpen,
    },
      h("summary", null,
        h("strong", null, event.tool_name || event.event_type || "trace event"),
        h("span", null, `turn ${(detail.unit && detail.unit.turn_index) || "?"}`),
        h("span", null, event.output_error ? "status: error" : "status: done"),
        event.duration_ms ? h("span", null, `${formatCount(event.duration_ms)} ms`) : null
      ),
      h("div", { className: "ae-trace-body" },
        h("div", { className: "ae-drawer-actions" },
          h(CopyButton, { value: event.id, label: "Copy event ID", title: "Copy event ID" }),
          h(CopyButton, { value: event.input_preview, label: "Copy args", title: "Copy args preview" }),
          h(CopyButton, { value: event.output_preview, label: "Copy result", title: "Copy result preview" }),
          h(CopyButton, { value: safePrettyJson(event.source_payload_json), label: "Copy raw", title: "Copy raw payload" })
        ),
        h(FeedbackButtons, {
          targetType: "case_event",
          targetId: event.id,
          evalUnitId: detail.unit && detail.unit.id,
          currentLabel: event.output_error ? "problem" : "ok",
          labels: ["problem", "ok", "unsure"],
          commentPrefix: "Marked tool call from dashboard",
        }),
        h("h4", null, "Args"), h("pre", { className: "ae-pre" }, event.input_preview || "—"),
        h("h4", null, "Result"), h("pre", { className: "ae-pre" }, event.output_preview || "—"),
        event.output_error ? h("p", { className: "ae-error-inline" }, "Tool call returned an error.") : null,
        h("h4", null, "Raw payload"), h("pre", { className: "ae-pre" }, safePrettyJson(event.source_payload_json))
      )
    );
  }

  function ToolCallsTab({ data, selectedUnitId, selectedEventId, setSelectedUnitId }) {
    const [errorsOnly, setErrorsOnly] = useState(false);
    const details = data.cases || [];
    const rows = [];
    details.forEach((detail) => (detail.case_events || []).forEach((event) => rows.push({ detail, event })));
    const filtered = errorsOnly ? rows.filter(({ event }) => event.output_error) : rows;
    return h("div", { className: "ae-section" },
      h("div", { className: "ae-filter-row" },
        h("label", null, h("input", { type: "checkbox", checked: errorsOnly, onChange: (ev) => setErrorsOnly(ev.target.checked) }), " Errors only"),
        h("span", { className: "ae-muted" }, `${formatCount(filtered.length)} trace events`)
      ),
      h("div", { className: "ae-trace-list" }, filtered.map(({ detail, event }) =>
        h("div", { key: event.id, onClick: () => setSelectedUnitId(detail.unit && detail.unit.id) },
          h(TraceEventRow, { event, detail, selectedEventId, selectedUnitId })
        )
      ))
    );
  }

  function JudgeEvalTab({ data, selectedUnitId, setSelectedUnitId }) {
    return h("div", { className: "ae-section" }, (data.cases || []).map((detail) => {
      const row = detail.unit || {};
      const evalRow = detail.latest_eval || {};
      const selected = row.id === selectedUnitId;
      return h("details", { className: `ae-judge-row ${selected ? "ae-turn-selected" : ""}`, key: row.id, open: selected },
        h("summary", { onClick: () => setSelectedUnitId(row.id) },
          h("strong", null, `Turn ${row.turn_index || "?"} · ${evalRow.outcome_status || "not judged"}`),
          h("span", null, evalRow.summary_reason || "No judge result")
        ),
        h("div", { className: "ae-drawer-summary-grid" },
          h("div", null, h("label", null, "Confidence"), h("strong", null, evalRow.confidence || "—")),
          h("div", null, h("label", null, "Judge"), h("strong", null, [evalRow.judge_provider, evalRow.judge_model].filter(Boolean).join(" / ") || "—")),
          h("div", null, h("label", null, "Tokens"), h("strong", null, formatCount(evalRow.review_total_tokens))),
          h("div", null, h("label", null, "Calls"), h("strong", null, formatCount(evalRow.judge_call_count)))
        ),
        h("h4", null, "Findings"),
        evalRow.id ? h(FeedbackButtons, {
          targetType: "case_review",
          targetId: evalRow.id,
          evalUnitId: row.id,
          currentLabel: evalRow.outcome_status,
          labels: ["succeed", "failed", "mishandled", "prolonged"],
          commentPrefix: "Marked LLM judge result from dashboard",
        }) : null,
        h(RawJsonBlock, { value: evalRow.findings || [], title: "Findings JSON" }),
        h("h4", null, "Raw eval JSON"),
        h(RawJsonBlock, { value: evalRow.eval_json || evalRow, title: "Eval JSON" })
      );
    }));
  }

  function RawTab({ data, selectedUnitId, selectedEventId }) {
    const detail = selectedDetail(data, selectedUnitId);
    const event = detail && (detail.case_events || []).find((row) => row.id === selectedEventId);
    return h("div", { className: "ae-section" },
      h(RawJsonBlock, { value: data, title: "Session detail JSON" }),
      detail ? h(RawJsonBlock, { value: detail, title: "Selected unit JSON" }) : null,
      event ? h(RawJsonBlock, { value: event, title: "Selected trace event JSON" }) : null
    );
  }

  function SessionDrawer({ drawerSessionId, since, initialUnitId, initialEventId, initialTab, onClose }) {
    const [drawerData, setDrawerData] = useState(null);
    const [drawerError, setDrawerError] = useState(null);
    const [drawerLoading, setDrawerLoading] = useState(false);
    const [selectedUnitId, setSelectedUnitId] = useState(initialUnitId || null);
    const [selectedEventId, setSelectedEventId] = useState(initialEventId || null);
    const [activeTab, setActiveTab] = useState(initialTab || "overview");

    useEffect(() => {
      setSelectedUnitId(initialUnitId || null);
      setSelectedEventId(initialEventId || null);
      setActiveTab(initialTab || "overview");
    }, [drawerSessionId, initialUnitId, initialEventId, initialTab]);

    useEffect(() => {
      if (!drawerSessionId) return undefined;
      const abort = new AbortController();
      setDrawerLoading(true);
      setDrawerError(null);
      setDrawerData(null);
      fetchPluginJSON(`/api/plugins/ariadne-eval/sessions/${encodeURIComponent(drawerSessionId)}?since=${encodeURIComponent(since)}&unit_limit=500`, { signal: abort.signal })
        .then((payload) => {
          setDrawerData(payload);
          if (!selectedUnitId && payload && payload.cases && payload.cases[0] && payload.cases[0].unit) {
            setSelectedUnitId(payload.cases[0].unit.id);
          }
        })
        .catch((err) => {
          if (err && err.name === "AbortError") return;
          setDrawerError(err && err.message ? err.message : String(err));
        })
        .finally(() => {
          if (!abort.signal.aborted) setDrawerLoading(false);
        });
      return () => abort.abort();
    }, [drawerSessionId, since]);

    useEffect(() => {
      if (!selectedEventId) return;
      const safeId = `ae-event-${String(selectedEventId || "").replace(/[^a-zA-Z0-9_-]/g, "-")}`;
      window.setTimeout(() => {
        const element = document.getElementById(safeId);
        if (element && element.scrollIntoView) element.scrollIntoView({ block: "center", behavior: "smooth" });
      }, 0);
    }, [drawerData, selectedEventId, activeTab]);

    useEffect(() => {
      function closeOnEscape(ev) {
        if (ev.key === "Escape") onClose();
      }
      window.addEventListener("keydown", closeOnEscape);
      return () => window.removeEventListener("keydown", closeOnEscape);
    }, [onClose]);

    if (!drawerSessionId) return null;
    const data = drawerData || {};
    const jumpTo = (unitId, eventId, tab) => {
      if (unitId) setSelectedUnitId(unitId);
      if (eventId) setSelectedEventId(eventId);
      if (tab) setActiveTab(tab);
    };

    return h("div", { className: "ae-drawer-shade", role: "presentation", onClick: onClose },
      h("aside", { className: "ae-drawer", role: "dialog", "aria-modal": "true", onClick: (ev) => ev.stopPropagation() },
        h("div", { className: "ae-drawer-head" },
          h("div", null,
            h("h2", null, data.title || drawerSessionId),
            h("div", { className: "ae-evidence-meta" },
              h("span", { className: "ae-copyable-id", title: drawerSessionId }, drawerSessionId),
              data.last_started_at ? h(LocalTime, { value: data.last_started_at }) : null,
              h("span", null, `${formatCount(data.turn_cases)} cases`),
              h("span", null, `${formatCount(data.evaluated_turns)} judged`),
              h("span", null, `${formatCount(data.tool_outcome_case_count)} tool outcome cases`),
              h("span", null, `${formatCount(data.finding_count)} findings`)
            )
          ),
          h("div", { className: "ae-drawer-actions" },
            h(CopyButton, { value: drawerSessionId, label: "Copy session ID", title: "Copy session ID" }),
            h("button", { type: "button", className: "ae-close-button", onClick: onClose }, "Close")
          )
        ),
        h(DrawerTabs, { activeTab, setActiveTab }),
        h("div", { className: "ae-drawer-body" },
          drawerLoading ? h("div", { className: "ae-empty" }, "Loading session detail…") : null,
          drawerError ? h("div", { className: "ae-error" }, drawerError) : null,
          drawerData && activeTab === "overview" ? h(SessionOverviewTab, { data, onJump: jumpTo }) : null,
          drawerData && activeTab === "turns" ? h(SessionTurnsTab, { data, selectedUnitId, setSelectedUnitId }) : null,
          drawerData && activeTab === "tools" ? h(ToolCallsTab, { data, selectedUnitId, selectedEventId, setSelectedUnitId }) : null,
          drawerData && activeTab === "judge" ? h(JudgeEvalTab, { data, selectedUnitId, setSelectedUnitId }) : null,
          drawerData && activeTab === "raw" ? h(RawTab, { data, selectedUnitId, selectedEventId }) : null
        )
      )
    );
  }

  function AriadneEvalDashboard() {
    const [since, setSince] = useState("24h");
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(false);
    const [tasks, setTasks] = useState([]);
    const [configOpen, setConfigOpen] = useState(false);
    const [config, setConfig] = useState(null);
    const [configLoading, setConfigLoading] = useState(false);
    const [configError, setConfigError] = useState(null);
    const [sessionPage, setSessionPage] = useState(0);
    const [drawerSessionId, setDrawerSessionId] = useState(null);
    const [drawerTarget, setDrawerTarget] = useState({ unitId: null, eventId: null, tab: "overview" });

    const sessions = useMemo(() => (data && (data.session_groups || data.hot_sessions)) || [], [data]);

    function load() {
      setLoading(true);
      setError(null);
      const sessionOffset = Math.max(0, Number(sessionPage || 0)) * SESSION_PAGE_SIZE;
      fetchPluginJSON(`/api/plugins/ariadne-eval/summary?since=${encodeURIComponent(since)}&bucket_seconds=3600&unit_limit=5000&session_limit=${SESSION_PAGE_SIZE}&session_offset=${sessionOffset}`)
        .then(setData)
        .catch((err) => setError(err && err.message ? err.message : String(err)))
        .finally(() => setLoading(false));
    }

    function loadTasks() {
      fetchPluginJSON("/api/plugins/ariadne-eval/review-jobs")
        .then((rows) => setTasks(rows || []))
        .catch(() => setTasks([]));
    }

    function applyConfig(payload) {
      setConfig(payload || null);
      setTasks((payload && payload.tasks) || []);
    }

    function loadConfig() {
      setConfigLoading(true);
      setConfigError(null);
      return fetchPluginJSON("/api/plugins/ariadne-eval/config/options")
        .then(applyConfig)
        .catch((err) => setConfigError(err && err.message ? err.message : String(err)))
        .finally(() => setConfigLoading(false));
    }

    function refreshTasksAndConfig() {
      loadTasks();
      loadConfig();
    }

    function taskAction(taskId, action) {
      postPluginJSON(`/api/plugins/ariadne-eval/review-jobs/${encodeURIComponent(taskId)}/${action}`, {})
        .then(refreshTasksAndConfig)
        .catch((err) => {
          const message = err && err.message ? err.message : String(err);
          setError(message);
          setConfigError(message);
        });
    }

    function onOpenSession(sessionId, target) {
      setDrawerTarget(Object.assign({ unitId: null, eventId: null, tab: "overview" }, target || {}));
      setDrawerSessionId(sessionId);
    }

    useEffect(load, [since, sessionPage]);
    useEffect(() => { loadTasks(); loadConfig(); }, []);

    const totals = (data && data.totals) || {};
    const tokens = (data && data.judge_tokens) || {};
    const sessionPagination = (data && data.session_pagination) || { limit: SESSION_PAGE_SIZE, offset: 0, total: sessions.length, has_next: false, has_prev: false };

    return h("div", { className: "ae-page" },
      h("div", { className: "ae-header" },
        h("div", null,
          h("h1", null, "Ariadne Eval"),
          h("p", null, "Request-first friction and evidence visualization for local instruction-health data."),
          h("p", { className: "ae-feedback-help" }, "Feedback is inline on requests, tool calls, problem evidence, and judge results; human corrections are recorded separately because deterministic rules and LLM judges can both be wrong.")
        ),
        h("div", { className: "ae-controls" },
          h("label", null, "Window"),
          h("select", {
            value: since,
            onChange: (ev) => {
              setSessionPage(0);
              setSince(ev.target.value);
            },
          },
            h("option", { value: "5h" }, "5 hours"),
            h("option", { value: "24h" }, "24 hours"),
            h("option", { value: "7d" }, "7 days"),
            h("option", { value: "" }, "All time")
          ),
          h("button", { onClick: load, disabled: loading }, loading ? "Refreshing…" : "Refresh"),
          h("button", { onClick: () => { setConfigOpen((open) => !open); if (!configOpen) loadConfig(); } }, configOpen ? "Hide config" : "Configure")
        )
      ),
      error ? h("div", { className: "ae-error" }, error) : null,
      configOpen ? h(ConfigPanel, {
        config,
        tasks,
        loading: configLoading,
        error: configError,
        onClose: () => setConfigOpen(false),
        onRefresh: loadConfig,
        onTaskSaved: refreshTasksAndConfig,
        onTaskAction: taskAction,
        onError: (message) => {
          setConfigError(message);
          setError(message);
        },
      }) : null,
      h("div", { className: "ae-stat-grid" },
        h(StatCard, { label: "Turn cases", value: totals.turn_cases }),
        h(StatCard, { label: "Judged turns", value: totals.evaluated_turns, sub: `${formatCount(tokens.total_tokens)} judge tokens` }),
        h(StatCard, { label: "Tool outcome cases", value: (data && data.tool_outcome_cases && data.tool_outcome_cases.length) || 0 }),
        h(StatCard, { label: "Judge findings", value: totals.findings })
      ),
      h("div", { className: "ae-grid" },
        h(FrictionSummary, { friction: data && data.friction, anchors: data && data.friction_anchors }),
        h("section", { className: "ae-panel ae-wide" },
          h("div", { className: "ae-panel-title-row" },
            h("h2", null, "Requests needing attention"),
            h("span", { className: "ae-muted" }, "Sorted by friction_score")
          ),
          h(RequestsNeedingAttention, { requests: data && data.requests, onOpenSession })
        ),
        h("section", { className: "ae-panel" }, h("h2", null, "Health statuses"), h(StatusBars, { statuses: data && data.statuses })),
        h("section", { className: "ae-panel" }, h("h2", null, "Tool outcome reviews"), h(ToolOutcomeReviewChips, { rows: data && data.tool_outcome_cases })),
        h("section", { className: "ae-panel" },
          h("div", { className: "ae-panel-title-row" },
            h("h2", null, "Recurring review jobs"),
            h("span", { className: "ae-muted" }, "Explicit controls")
          ),
          h(EvalTaskControls, { tasks, onAction: taskAction })
        ),
        h("section", { className: "ae-panel" }, h("h2", null, "Top findings"), h(ChipList, { rows: data && data.top_findings, labelKey: "finding_type" })),
        h("section", { className: "ae-panel ae-wide" }, h("h2", null, "Finding timeline"), h(Timeline, { timeline: data && data.timeline }))
      ),
      h("section", { className: "ae-panel ae-wide" },
        h("div", { className: "ae-panel-title-row" },
          h("h2", null, "Agent sessions"),
          h("span", { className: "ae-muted" }, `${formatCount(sessionPagination.total)} sessions grouped after request ranking`)
        ),
        h(SessionPaginationControls, {
          pagination: sessionPagination,
          onPage: (offset) => setSessionPage(Math.floor(Math.max(0, offset) / SESSION_PAGE_SIZE)),
        }),
        h(SessionGroups, { sessions, onOpenSession })
      ),
      h(SessionDrawer, {
        drawerSessionId,
        since,
        initialUnitId: drawerTarget.unitId,
        initialEventId: drawerTarget.eventId,
        initialTab: drawerTarget.tab,
        onClose: () => setDrawerSessionId(null),
      })
    );
  }

  window.__HERMES_PLUGINS__.register("ariadne-eval", AriadneEvalDashboard);
})();
