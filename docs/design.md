     1|# Agent Instruction Health Evaluator — Design Document
     2|
     3|Date: 2026-05-19
     4|Status: concrete MVP design draft
     5|Project name: intentionally omitted from this document
     6|Primary target: Hermes Agent users
     7|Secondary target: future agent-framework adapters
     8|
     9|## 1. Overview
    10|
    11|This project is a lightweight, local-first developer tool for evaluating the health of agent task runs. It is not a generic LLM observability product and it is not intended to replace full trace platforms. The core use case is: after an agent session or conversation, identify which user requests were handled cleanly, which were failed, which were mishandled, and which completed only after unnecessary or strange steps.
    12|
    13|The first implementation should focus on Hermes Agent because Hermes is the priority integration and already stores rich session data. The design should remain agent-agnostic enough that OpenClaw or other agents can later provide adapters, but the MVP should not wait for a perfect universal abstraction.
    14|
    15|The evaluator should process every run or user request, but it should not run in real time. A timed batch is cheaper, simpler, and better suited to using the next user message as evidence of whether the previous turn went badly.
    16|
    17|The recommended MVP is:
    18|
    19|```text
    20|Hermes native plugin
    21|  -> passive hook event capture
    22|  -> Hermes state.db reader
    23|  -> normalized user-turn evaluation units
    24|  -> deterministic trace signals
    25|  -> LLM judge using existing Hermes model connection
    26|  -> sidecar local eval database
    27|  -> CLI visualizer for failed / mishandled / prolonged runs
    28|```
    29|
    30|## 2. Summary of the conversation that led to this design
    31|
    32|The initial need was a tool that can visualise and summarise mistakes made by an agent. Langfuse was considered because Hermes has a Langfuse observability plugin, but that path is too heavy for this goal. Langfuse is primarily an LLM observability and evaluation platform. It requires trace setup, evaluator setup, and usually another LLM connection for LLM-as-judge. It can show traces, but it does not automatically provide a lightweight local agent-health loop.
    33|
    34|The project gap is therefore:
    35|
    36|```text
    37|A local, lightweight, agent-focused evaluator that can inspect full agent traces,
    38|identify failed or bumpy task runs, and eventually turn user feedback into
    39|self-improvement leads.
    40|```
    41|
    42|The project should especially focus on:
    43|
    44|- failed attempts at achieving a user goal;
    45|- mishandled tasks where the agent misunderstood, over-claimed, used tools badly, or required user correction;
    46|- prolonged tasks where the agent eventually succeeds but takes unnecessary or strange steps;
    47|- user reaction as evidence, especially the next user turn after an agent response;
    48|- local storage inside the agent container or profile directory;
    49|- using the existing Hermes model configuration rather than forcing a new LLM API setup.
    50|
    51|Current design focus is evaluation and visualisation. User feedback and self-improvement are important future directions, but they should not overload the MVP.
    52|
    53|## 3. Goals
    54|
    55|The MVP must do the following.
    56|
    57|1. Work with Hermes Agent first.
    58|2. Run locally inside or beside the Hermes profile/container.
    59|3. Use Hermes session records as the primary source of truth.
    60|4. Capture enough hook-level event data to reason about tool errors, tool duration, and strange loops.
    61|5. Evaluate every user request in a conversation, not just whole sessions.
    62|6. Use full trace context, not only the final answer.
    63|7. Use the next user message as retrospective evidence when available.
    64|8. Produce structured JSON evaluations that can be queried and visualised.
    65|9. Classify each evaluation unit into one primary health status: `succeed`, `failed`, `mishandled`, `prolonged`, or `not_evaluable`.
    66|10. Avoid Langfuse, hosted dashboards, and extra evaluator-specific API credentials.
    67|11. Avoid storing or using hidden chain-of-thought or provider reasoning fields in the normalized evaluation records.
    68|
    69|## 4. Non-goals for MVP
    70|
    71|The MVP should not attempt to do the following.
    72|
    73|- Build a full web dashboard.
    74|- Replace Langfuse as a general LLM observability platform.
    75|- Evaluate safety or policy compliance.
    76|- Automatically modify Hermes prompts, skills, memories, or configs.
    77|- Build a full user-feedback learning loop.
    78|- Support teams, collaboration, cloud hosting, or multi-user deployments.
    79|- Provide a polished report format before the core framework works.
    80|- Support OpenClaw natively before the Hermes path is working.
    81|- Require a local model to exist before the project can run.
    82|
    83|## 5. Hermes integration assumptions
    84|
    85|The Hermes-first design is based on the following currently documented Hermes surfaces.
    86|
    87|Hermes stores sessions in `~/.hermes/state.db`, a SQLite database. That database includes session metadata, full message history, model configuration, token counts, tool call counts, timestamps, lineage, and full-text search tables.
    88|
    89|Hermes also supports native plugin hooks that fire in CLI and gateway sessions. The useful hook events for this project are:
    90|
    91|```text
    92|on_session_start
    93|pre_llm_call
    94|post_llm_call
    95|pre_tool_call
    96|post_tool_call
    97|on_session_end
    98|on_session_finalize
    99|post_approval_response
   100|```
   101|
   102|Hermes provider/runtime resolution is shared across normal chat, gateway, cron, ACP, and auxiliary model calls. The evaluator should use this existing provider configuration rather than introducing a separate mandatory LLM API key.
   103|
   104|Design consequence: the MVP should be a Hermes-native plugin plus a batch evaluator that reads Hermes DB records. Hooks should be passive and fast. Expensive LLM judging should happen later in a timed batch.
   105|
   106|## 6. Recommended ingestion mode
   107|
   108|Based on the constraints, the recommended ingestion mode is:
   109|
   110|```text
   111|Native Hermes plugin + timed batch over Hermes state.db
   112|```
   113|
   114|Not pure offline import, because the project needs duration and event-level trace signals that may not be fully represented in the session DB.
   115|
   116|Not realtime tracing, because that is expensive, unnecessary, and too close to a Langfuse-style observability service.
   117|
   118|Not manual pasted transcript, because Hermes already stores the relevant conversation and tool records.
   119|
   120|The plugin should do two things only:
   121|
   122|1. Register passive hooks and write lightweight event records.
   123|2. Mark sessions or turns as dirty/evaluation-due.
   124|
   125|The batch evaluator should do the expensive work:
   126|
   127|1. Read `~/.hermes/state.db`.
   128|2. Join with the plugin event cache.
   129|3. Normalize sessions into user-turn evaluation units.
   130|4. Compute deterministic signals.
   131|5. Summarize the trace.
   132|6. Call the judge model.
   133|7. Store structured evals in the sidecar evaluation DB.
   134|
   135|## 7. High-level architecture
   136|
   137|```mermaid
   138|graph TD
   139|    U[User talks to Hermes] --> H[Hermes agent loop]
   140|    H --> HS[Hermes state.db]
   141|    H --> PH[Instruction-health plugin hooks]
   142|    PH --> EC[Local event cache]
   143|    HS --> N[Hermes adapter / normalizer]
   144|    EC --> N
   145|    N --> EU[Evaluation units: one user request + response + trace + reaction]
   146|    EU --> D[Deterministic signal extractor]
   147|    D --> S[Trace summarizer]
   148|    S --> J[LLM judge via existing Hermes provider config]
   149|    J --> EDB[Sidecar eval database]
   150|    EDB --> CLI[CLI visualizer]
   151|    EDB --> FUT[Future feedback / self-improvement layer]
   152|```
   153|
   154|## 8. Evaluation unit definition
   155|
   156|The evaluator should not score only whole sessions. A session can contain many user requests. It should score each user request as an evaluation unit.
   157|
   158|An evaluation unit is anchored on one user message and includes:
   159|
   160|```text
   161|current user request
   162|previous conversation context needed to interpret it
   163|assistant final response for that turn
   164|tool calls and tool results between request and response
   165|deterministic trace signals
   166|next user message, if available, as user reaction evidence
   167|session metadata
   168|model/provider metadata
   169|```
   170|
   171|The next user message is important. Many failures are only obvious when the user says something like:
   172|
   173|```text
   174|No, that is not what I asked.
   175|You did not create the file.
   176|Why did you search the web?
   177|This took too long.
   178|Can you actually finish it?
   179|```
   180|
   181|The evaluator should therefore use a retrospective lag policy.
   182|
   183|A user-turn evaluation unit becomes due when any of these conditions is met:
   184|
   185|1. There is a next user message after the assistant response.
   186|2. The session has ended and a configurable cooldown has passed.
   187|3. The session is inactive for a configurable cooldown.
   188|4. The turn is explicitly requested for evaluation by CLI.
   189|
   190|Recommended defaults:
   191|
   192|```yaml
   193|evaluation:
   194|  cooldown_minutes_after_session_end: 30
   195|  cooldown_minutes_after_inactive_turn: 120
   196|  evaluate_last_turn_without_reaction: true
   197|  reevaluate_previous_turn_when_next_user_reaction_arrives: true
   198|```
   199|
   200|## 9. Health status taxonomy
   201|
   202|Each evaluation unit gets one primary `health_status`.
   203|
   204|### 9.1 `succeed`
   205|
   206|The agent achieved the user’s apparent goal without meaningful avoidable friction.
   207|
   208|Examples:
   209|
   210|- The user asked for a file and the trace shows it was created.
   211|- The user asked for a summary and the assistant provided one consistent with available context.
   212|- The next user message continues naturally or accepts the result.
   213|
   214|### 9.2 `failed`
   215|
   216|The agent did not achieve the goal.
   217|
   218|Examples:
   219|
   220|- The tool failed and the final response did not recover.
   221|- The assistant never completed the requested task.
   222|- The user explicitly says the goal was not achieved.
   223|- The agent ends with inability but the trace suggests it did not exhaust a reasonable path.
   224|
   225|### 9.3 `mishandled`
   226|
   227|The agent attempted or partially achieved the task but handled it incorrectly.
   228|
   229|Examples:
   230|
   231|- Misunderstood the user’s intent.
   232|- Used the wrong tool.
   233|- Failed to use a necessary tool.
   234|- Claimed an external action succeeded without trace evidence.
   235|- Ignored important context from earlier in the conversation.
   236|- Over-refused or gave an unhelpfully vague answer and the user pushed back.
   237|- Required a user correction that should have been unnecessary.
   238|
   239|### 9.4 `prolonged`
   240|
   241|The agent likely achieved or approached the goal, but the path was unnecessarily long, loopy, or strange.
   242|
   243|Examples:
   244|
   245|- Repeated the same tool call with the same or near-identical arguments.
   246|- Used many tool calls for a simple answer.
   247|- Spent excessive time or API calls before producing a simple result.
   248|- Took detours unrelated to the request.
   249|- Succeeded only after avoidable retries.
   250|
   251|### 9.5 `not_evaluable`
   252|
   253|There is not enough evidence to judge the run.
   254|
   255|Examples:
   256|
   257|- The user request was too ambiguous.
   258|- The tool output was missing or truncated beyond usefulness.
   259|- The session ended before an assistant response.
   260|- The user changed the goal before completion.
   261|- The trace lacks enough context to distinguish success from failure.
   262|
   263|### 9.6 Status precedence
   264|
   265|When multiple statuses seem plausible, use this precedence:
   266|
   267|```text
   268|failed > mishandled > prolonged > succeed > not_evaluable
   269|```
   270|
   271|`not_evaluable` only wins when the evidence is insufficient. A run can be both prolonged and mishandled internally, but the primary status should be `mishandled` if the strange path caused a wrong or user-hostile outcome.
   272|
   273|## 10. Barrier taxonomy
   274|
   275|The primary health status is single-value. Barriers are multi-value evidence tags that explain why the status was assigned.
   276|
   277|Recommended MVP barrier types:
   278|
   279|```text
   280|tool_error
   281|repeated_tool_loop
   282|unnecessary_tool_use
   283|missing_tool_use
   284|bad_tool_selection
   285|external_action_not_verified
   286|action_misrepresentation
   287|misread_instruction
   288|missed_requirement
   289|unsupported_claim
   290|format_mismatch
   291|vague_or_incomplete_response
   292|over_refusal
   293|under_clarification
   294|user_correction
   295|user_repeated_request
   296|interrupted_or_incomplete
   297|excessive_duration
   298|excessive_api_calls
   299|excessive_tool_calls
   300|context_loss
   301|```
   302|
   303|Do not over-normalize these too early. Store them as strings, and allow taxonomy changes later.
   304|
   305|## 11. Deterministic signal extraction
   306|
   307|The LLM judge should not be the only source of truth. Before judging, the system should compute deterministic signals from the trace.
   308|
   309|MVP deterministic signals:
   310|
   311|| Signal | Source | Purpose |
   312||---|---|---|
   313|| `tool_call_count` | session DB + hook events | Detect complexity and prolonged runs |
   314|| `api_call_count` | sessions table if available | Detect long loops |
   315|| `turn_duration_seconds` | timestamps + hook events | Detect prolonged runs |
   316|| `same_tool_repeat_count` | hook events | Detect repeated loops |
   317|| `tool_error_count` | tool result JSON / content | Detect tool failures |
   318|| `terminal_nonzero_exit_count` | terminal hook result if available | Detect command failures |
   319|| `approval_denied_count` | approval hook | Detect blocked actions |
   320|| `assistant_claimed_completion` | final response heuristic | Check against tool evidence |
   321|| `next_user_reaction_type` | next user message | Detect correction or dissatisfaction |
   322|| `format_requested` | user request heuristic / LLM extraction | Check format compliance |
   323|| `format_delivered` | final answer parser | Check format mismatch |
   324|
   325|Suggested thresholds for MVP:
   326|
   327|```yaml
   328|thresholds:
   329|  prolonged_tool_calls: 8
   330|  prolonged_api_calls: 4
   331|  prolonged_turn_minutes: 10
   332|  repeated_same_tool_same_args: 3
   333|  long_tool_result_chars: 8000
   334|```
   335|
   336|These thresholds should be configurable because coding tasks and research tasks have different normal ranges.
   337|
   338|## 12. User reaction inference
   339|
   340|The next user message should be classified before being given to the judge.
   341|
   342|Suggested reaction categories:
   343|
   344|```text
   345|acceptance
   346|continuation
   347|clarification
   348|correction
   349|complaint
   350|repeated_request
   351|scope_change
   352|unrelated
   353|unknown
   354|```
   355|
   356|Rules:
   357|
   358|1. Do not assume every correction means the agent failed. The user may be clarifying an ambiguous original request.
   359|2. Do not assume every continuation means success. The user may be continuing because the agent did not finish.
   360|3. Give the judge both the next user message and the deterministic reaction classification.
   361|4. Allow the judge to override the deterministic classification with explanation.
   362|
   363|Example deterministic patterns:
   364|
   365|```text
   366|correction: "no", "not what", "you didn't", "that's wrong", "actually", "I meant"
   367|complaint: "this is terrible", "why", "too hard", "too complicated", "not useful"
   368|repeated_request: high lexical overlap with previous user request
   369|acceptance: "thanks", "great", "that works", "yes"
   370|scope_change: "now", "next", "also", "can we add"
   371|```
   372|
   373|## 13. Data sources in Hermes
   374|
   375|### 13.1 Primary source: Hermes `state.db`
   376|
   377|Use Hermes `~/.hermes/state.db` as the main data source.
   378|
   379|Important fields from `sessions`:
   380|
   381|```text
   382|id
   383|source
   384|user_id
   385|model
   386|model_config
   387|system_prompt
   388|parent_session_id
   389|started_at
   390|ended_at
   391|end_reason
   392|message_count
   393|tool_call_count
   394|input_tokens
   395|output_tokens
   396|reasoning_tokens
   397|estimated_cost_usd
   398|actual_cost_usd
   399|title
   400|api_call_count
   401|```
   402|
   403|Important fields from `messages`:
   404|
   405|```text
   406|id
   407|session_id
   408|role
   409|content
   410|tool_call_id
   411|tool_calls
   412|tool_name
   413|timestamp
   414|token_count
   415|finish_reason
   416|```
   417|
   418|Fields to exclude by default:
   419|
   420|```text
   421|reasoning
   422|reasoning_content
   423|reasoning_details
   424|codex_reasoning_items
   425|codex_message_items
   426|```
   427|
   428|Reason: even if providers expose reasoning fields, this tool should not depend on or display hidden reasoning. Evaluation should use user-visible messages, tool calls, tool results, and metadata.
   429|
   430|### 13.2 Supplemental source: plugin event cache
   431|
   432|Hermes DB records are necessary but not always enough. The plugin should write lightweight event records to a sidecar event cache.
   433|
   434|Suggested path:
   435|
   436|```text
   437|$HERMES_HOME/instruction-health/events.jsonl
   438|```
   439|
   440|Later, this can become SQLite if needed.
   441|
   442|Event types:
   443|
   444|```text
   445|session_start
   446|pre_llm_call
   447|post_llm_call
   448|tool_start
   449|tool_end
   450|session_end
   451|session_finalize
   452|approval_response
   453|```
   454|
   455|Event schema:
   456|
   457|```json
   458|{
   459|  "event_id": "evt_...",
   460|  "schema_version": "event_v1",
   461|  "framework": "hermes",
   462|  "session_id": "...",
   463|  "event_type": "tool_end",
   464|  "timestamp": 1779180000.123,
   465|  "payload": {
   466|    "tool_name": "terminal",
   467|    "args_hash": "sha256:...",
   468|    "args_preview": "...",
   469|    "result_error": true,
   470|    "result_preview": "...",
   471|    "duration_ms": 842
   472|  }
   473|}
   474|```
   475|
   476|Payload previews should be capped to avoid storing huge tool outputs twice.
   477|
   478|Recommended caps:
   479|
   480|```yaml
   481|capture:
   482|  max_args_chars: 4000
   483|  max_result_preview_chars: 4000
   484|  hash_full_args: true
   485|  hash_full_result: true
   486|```
   487|
   488|## 14. Sidecar evaluation database
   489|
   490|Use a sidecar SQLite database for MVP, not JSONL.
   491|
   492|Reason: the source of truth is already SQLite, and the visualizer will need indexed queries over statuses, barriers, sessions, dates, and models.
   493|
   494|Suggested path:
   495|
   496|```text
   497|$HERMES_HOME/instruction-health/evals.db
   498|```
   499|
   500|### 14.1 Table: `eval_units`
   501|
   502|```sql
   503|CREATE TABLE eval_units (
   504|    id TEXT PRIMARY KEY,
   505|    framework TEXT NOT NULL,
   506|    source_session_id TEXT NOT NULL,
   507|    source_turn_index INTEGER NOT NULL,
   508|    user_message_id TEXT NOT NULL,
   509|    assistant_message_id TEXT,
   510|    next_user_message_id TEXT,
   511|    started_at REAL,
   512|    ended_at REAL,
   513|    source TEXT,
   514|    model TEXT,
   515|    title TEXT,
   516|    parent_session_id TEXT,
   517|    user_request TEXT NOT NULL,
   518|    assistant_response TEXT,
   519|    previous_context_summary TEXT,
   520|    next_user_reaction_text TEXT,
   521|    tool_call_count INTEGER DEFAULT 0,
   522|    api_call_count INTEGER DEFAULT 0,
   523|    input_tokens INTEGER DEFAULT 0,
   524|    output_tokens INTEGER DEFAULT 0,
   525|    normalization_version TEXT NOT NULL,
   526|    created_at REAL NOT NULL,
   527|    updated_at REAL NOT NULL,
   528|    UNIQUE(framework, source_session_id, source_turn_index)
   529|);
   530|```
   531|
   532|### 14.2 Table: `trace_events`
   533|
   534|```sql
   535|CREATE TABLE trace_events (
   536|    id TEXT PRIMARY KEY,
   537|    eval_unit_id TEXT NOT NULL REFERENCES eval_units(id),
   538|    source_event_id TEXT,
   539|    event_type TEXT NOT NULL,
   540|    timestamp REAL,
   541|    tool_name TEXT,
   542|    args_hash TEXT,
   543|    args_preview TEXT,
   544|    result_hash TEXT,
   545|    result_preview TEXT,
   546|    result_error INTEGER DEFAULT 0,
   547|    duration_ms INTEGER,
   548|    raw_payload_json TEXT
   549|);
   550|```
   551|
   552|### 14.3 Table: `deterministic_signals`
   553|
   554|```sql
   555|CREATE TABLE deterministic_signals (
   556|    id INTEGER PRIMARY KEY AUTOINCREMENT,
   557|    eval_unit_id TEXT NOT NULL REFERENCES eval_units(id),
   558|    signal_name TEXT NOT NULL,
   559|    signal_value TEXT NOT NULL,
   560|    severity TEXT,
   561|    evidence TEXT,
   562|    created_at REAL NOT NULL
   563|);
   564|```
   565|
   566|### 14.4 Table: `llm_evals`
   567|
   568|```sql
   569|CREATE TABLE llm_evals (
   570|    id TEXT PRIMARY KEY,
   571|    eval_unit_id TEXT NOT NULL REFERENCES eval_units(id),
   572|    prompt_version TEXT NOT NULL,
   573|    judge_provider TEXT,
   574|    judge_model TEXT,
   575|    health_status TEXT NOT NULL,
   576|    confidence TEXT NOT NULL,
   577|    primary_reason TEXT NOT NULL,
   578|    eval_json TEXT NOT NULL,
   579|    evaluator_error TEXT,
   580|    created_at REAL NOT NULL
   581|);
   582|```
   583|
   584|### 14.5 Table: `barriers`
   585|
   586|```sql
   587|CREATE TABLE barriers (
   588|    id INTEGER PRIMARY KEY AUTOINCREMENT,
   589|    eval_id TEXT NOT NULL REFERENCES llm_evals(id),
   590|    eval_unit_id TEXT NOT NULL REFERENCES eval_units(id),
   591|    barrier_type TEXT NOT NULL,
   592|    severity TEXT NOT NULL,
   593|    evidence TEXT,
   594|    source TEXT,
   595|    related_event_id TEXT
   596|);
   597|```
   598|
   599|### 14.6 Table: `eval_state`
   600|
   601|```sql
   602|CREATE TABLE eval_state (
   603|    key TEXT PRIMARY KEY,
   604|    value TEXT NOT NULL,
   605|    updated_at REAL NOT NULL
   606|);
   607|```
   608|
   609|Use `eval_state` for cursors such as last evaluated session timestamp, prompt version, and schema version.
   610|
   611|## 15. Hermes plugin design
   612|
   613|### 15.1 Plugin location
   614|
   615|Suggested development install path:
   616|
   617|```text
   618|~/.hermes/plugins/instruction-health/
   619|```
   620|
   621|Suggested manifest:
   622|
   623|```yaml
   624|name: instruction-health
   625|version: 0.1.0
   626|description: Local agent instruction-health evaluator for Hermes sessions
   627|provides_hooks:
   628|  - on_session_start
   629|  - pre_llm_call
   630|  - post_llm_call
   631|  - pre_tool_call
   632|  - post_tool_call
   633|  - on_session_end
   634|  - on_session_finalize
   635|  - post_approval_response
   636|```
   637|
   638|No model-visible tools should be exposed in MVP. The plugin should not change agent behavior. It should not inject context. It should not transform tool results. It should only observe and write sidecar records.
   639|
   640|### 15.2 Hook registrations
   641|
   642|```python
   643|# __init__.py
   644|
   645|def register(ctx):
   646|    ctx.register_hook("on_session_start", on_session_start)
   647|    ctx.register_hook("pre_llm_call", pre_llm_call)
   648|    ctx.register_hook("post_llm_call", post_llm_call)
   649|    ctx.register_hook("pre_tool_call", pre_tool_call)
   650|    ctx.register_hook("post_tool_call", post_tool_call)
   651|    ctx.register_hook("on_session_end", on_session_end)
   652|    ctx.register_hook("on_session_finalize", on_session_finalize)
   653|    ctx.register_hook("post_approval_response", post_approval_response)
   654|```
   655|
   656|### 15.3 Hook behavior
   657|
   658|| Hook | Capture | Notes |
   659||---|---|---|
   660|| `on_session_start` | session id, model, platform, start timestamp | initialize local session buffer |
   661|| `pre_llm_call` | session id, user message preview, history length, model, platform | mark a new user turn boundary |
   662|| `post_llm_call` | assistant response preview, model, platform | mark final response for turn |
   663|| `pre_tool_call` | tool name, args preview/hash, task id | record tool start |
   664|| `post_tool_call` | tool name, args hash, result preview/hash, error heuristic, duration | record tool end and latency |
   665|| `post_approval_response` | command preview, approval choice | useful for blocked/denied actions |
   666|| `on_session_end` | completed/interrupted, model, platform | mark session dirty for eval |
   667|| `on_session_finalize` | session id/platform | final flush point |
   668|
   669|### 15.4 Performance requirements
   670|
   671|Hook code must be fast and fail-open.
   672|
   673|Rules:
   674|
   675|```text
   676|Do not call an LLM inside hooks.
   677|Do not perform slow DB scans inside hooks.
   678|Do not block the agent loop for evaluation.
   679|Do not mutate the prompt or tool output.
   680|Write short append-only records and return.
   681|Catch exceptions and log only local warnings.
   682|```
   683|
   684|## 16. Normalization algorithm
   685|
   686|The normalizer converts raw Hermes session data into evaluation units.
   687|
   688|### 16.1 Load session
   689|
   690|Input:
   691|
   692|```text
   693|session_id
   694|```
   695|
   696|Steps:
   697|
   698|1. Read session row from `sessions`.
   699|2. Read messages for the session ordered by `(timestamp, id)`.
   700|3. If `parent_session_id` exists, optionally load lineage context summary.
   701|4. Join available plugin event records by session id and timestamp windows.
   702|5. Exclude reasoning fields.
   703|
   704|### 16.2 Identify user-turn boundaries
   705|
   706|Basic rule:
   707|
   708|```text
   709|Each user message starts a candidate eval unit.
   710|The assistant response that follows it closes the eval unit.
   711|Tool calls/results between them belong to that eval unit.
   712|The next user message after the assistant response is reaction evidence.
   713|```
   714|
   715|Pseudocode:
   716|
   717|```python
   718|def normalize_session(messages):
   719|    units = []
   720|    for i, msg in enumerate(messages):
   721|        if msg.role != "user":
   722|            continue
   723|
   724|        user_msg = msg
   725|        assistant_msg = find_next_assistant_final(messages, start=i + 1)
   726|        next_user_msg = find_next_user_after(messages, assistant_msg)
   727|        tool_events = collect_tool_events_between(messages, user_msg, assistant_msg)
   728|        context = collect_previous_context(messages, before=i, max_turns=6)
   729|
   730|        units.append(EvalUnit(
   731|            user_message=user_msg,
   732|            assistant_message=assistant_msg,
   733|            next_user_message=next_user_msg,
   734|            previous_context=context,
   735|            tool_events=tool_events,
   736|        ))
   737|    return units
   738|```
   739|
   740|### 16.3 Conversation context
   741|
   742|For each eval unit, include enough previous context for the judge to understand pronouns, references, and corrections.
   743|
   744|Recommended MVP strategy:
   745|
   746|```text
   747|Include the previous 3 user/assistant pairs verbatim if small.
   748|If larger than max chars, summarize previous context using a cheap summary prompt.
   749|Always include the current user request verbatim.
   750|Always include the final assistant response verbatim up to configured cap.
   751|Summarize tool outputs instead of passing raw full output.
   752|```
   753|
   754|Suggested caps:
   755|
   756|```yaml
   757|context:
   758|  previous_turn_pairs: 3
   759|  max_previous_context_chars: 6000
   760|  max_user_request_chars: 4000
   761|  max_assistant_response_chars: 8000
   762|  max_tool_result_chars_per_call: 1500
   763|  max_total_judge_input_chars: 24000
   764|```
   765|
   766|## 17. Trace summarization
   767|
   768|The judge should receive a compact trace, not raw logs.
   769|
   770|Trace summary format:
   771|
   772|```json
   773|{
   774|  "tool_sequence": [
   775|    {
   776|      "index": 1,
   777|      "tool_name": "terminal",
   778|      "args_summary": "ran pytest",
   779|      "result_summary": "failed: 3 tests failed",
   780|      "error": true,
   781|      "duration_ms": 1200
   782|    }
   783|  ],
   784|  "deterministic_signals": [
   785|    {
   786|      "name": "tool_error_count",
   787|      "value": 3,
   788|      "severity": "medium",
   789|      "evidence": "terminal returned errors in calls 2, 3, and 4"
   790|    }
   791|  ],
   792|  "timing": {
   793|    "turn_duration_seconds": 740,
   794|    "api_call_count": 6,
   795|    "tool_call_count": 11
   796|  },
   797|  "next_user_reaction": {
   798|    "type": "correction",
   799|    "text": "No, you didn't create the file."
   800|  }
   801|}
   802|```
   803|
   804|Trace summarization can be deterministic at first. Do not use another LLM call just to summarize unless the trace is too large.
   805|
   806|## 18. LLM judge design
   807|
   808|### 18.1 Connection strategy
   809|
   810|The judge should use the existing Hermes model connection by default.
   811|
   812|Recommended config:
   813|
   814|```yaml
   815|instruction_health:
   816|  judge:
   817|    provider: main
   818|    model: main
   819|    temperature: 0
   820|    max_retries: 2
   821|    prompt_version: instruction_health_v1
   822|```
   823|
   824|`provider: main` and `model: main` mean: use the same provider/model resolution that Hermes uses for the active profile, unless the user explicitly configures a different judge model.
   825|
   826|Important caveat: local-first storage does not automatically mean local-only inference. If the current Hermes model is a remote provider, evaluation data will be sent to that same provider. That is still simpler than requiring a separate Langfuse evaluator API key, but it is not fully local inference unless Hermes itself is configured to use a local endpoint.
   827|
   828|### 18.2 Judge output must be strict JSON
   829|
   830|Structured output is mandatory. Visualisation depends on queryable fields. Natural language is allowed only inside JSON fields such as `primary_reason` and `evidence`.
   831|
   832|Invalid JSON handling:
   833|
   834|1. Try to parse the model output.
   835|2. If parsing fails, retry with a repair instruction and the invalid output.
   836|3. If parsing still fails, store an `evaluator_error` row and keep deterministic signals.
   837|
   838|### 18.3 Judge prompt
   839|
   840|```text
   841|You are evaluating the health of an AI agent's handling of one user request.
   842|
   843|Assess the agent charitably but evidence-first. The user may clarify ambiguous intent.
   844|Do not mark a run as bad merely because the user added a new request later.
   845|Do mark a run as mishandled if the next user message shows that the agent misunderstood,
   846|failed to complete the requested action, over-claimed completion, used tools badly, or gave
   847|a response that forced unnecessary correction.
   848|
   849|Use the full trace, not only the final answer.
   850|Do not reward verbosity.
   851|Do not infer that an external action succeeded unless the trace supports it.
   852|Do not use hidden reasoning. Only use the supplied user messages, assistant messages,
   853|tool trace summary, metadata, and deterministic signals.
   854|
   855|Return exactly one JSON object matching this schema:
   856|
   857|{
   858|  "schema_version": "instruction_health_eval_v1",
   859|  "health_status": "succeed | failed | mishandled | prolonged | not_evaluable",
   860|  "confidence": "high | medium | low",
   861|  "goal_summary": "one sentence",
   862|  "observed_outcome": "one sentence",
   863|  "primary_reason": "one sentence",
   864|  "user_reaction": {
   865|    "type": "acceptance | continuation | clarification | correction | complaint | repeated_request | scope_change | unrelated | unknown | none",
   866|    "used_as_evidence": true,
   867|    "evidence": "short quote or paraphrase"
   868|  },
   869|  "barriers": [
   870|    {
   871|      "type": "tool_error | repeated_tool_loop | unnecessary_tool_use | missing_tool_use | bad_tool_selection | external_action_not_verified | action_misrepresentation | misread_instruction | missed_requirement | unsupported_claim | format_mismatch | vague_or_incomplete_response | over_refusal | under_clarification | user_correction | user_repeated_request | interrupted_or_incomplete | excessive_duration | excessive_api_calls | excessive_tool_calls | context_loss",
   872|      "severity": "low | medium | high",
   873|      "source": "trace | assistant_response | user_reaction | deterministic_signal",
   874|      "evidence": "short evidence statement"
   875|    }
   876|  ],
   877|  "prolongation_evidence": {
   878|    "tool_calls": 0,
   879|    "api_calls": 0,
   880|    "duration_seconds": null,
   881|    "repeated_actions": []
   882|  },
   883|  "missed_or_mishandled_requirements": [],
   884|  "not_evaluable_reason": null
   885|}
   886|
   887|Status definitions:
   888|- succeed: the user goal was achieved without meaningful avoidable friction.
   889|- failed: the user goal was not achieved.
   890|- mishandled: the agent attempted or partially achieved the goal but misunderstood, over-claimed, used bad tools, missed requirements, or needed avoidable correction.
   891|- prolonged: the goal was achieved or nearly achieved but the trace contains unnecessary loops, detours, or excessive steps.
   892|- not_evaluable: there is insufficient evidence to judge.
   893|
   894|Status precedence:
   895|failed > mishandled > prolonged > succeed > not_evaluable.
   896|Use not_evaluable only when evidence is insufficient.
   897|
   898|Input:
   899|{{eval_unit_json}}
   900|```
   901|
   902|## 19. CLI design
   903|
   904|Use a placeholder command name until the repo name is chosen. In examples below, use `agent-health`.
   905|
   906|### 19.1 Initialization
   907|
   908|```bash
   909|agent-health init --hermes-home ~/.hermes
   910|```
   911|
   912|Creates:
   913|
   914|```text
   915|~/.hermes/instruction-health/
   916|  config.yaml
   917|  events.jsonl
   918|  evals.db
   919|  logs/
   920|```
   921|
   922|### 19.2 Plugin installation
   923|
   924|```bash
   925|agent-health hermes install-plugin --hermes-home ~/.hermes
   926|hermes plugins enable instruction-health
   927|```
   928|
   929|### 19.3 Import and normalize Hermes sessions
   930|
   931|```bash
   932|agent-health import hermes --hermes-home ~/.hermes --since 7d
   933|```
   934|
   935|### 19.4 Run due evaluations
   936|
   937|```bash
   938|agent-health eval --due --limit 50
   939|```
   940|
   941|### 19.5 Show bumpy runs
   942|
   943|```bash
   944|agent-health list --status failed,mishandled,prolonged --since 7d
   945|```
   946|
   947|Example output:
   948|
   949|```text
   950|TIME                STATUS       SESSION       TURN  BARRIERS                         REQUEST
   951|2026-05-19 10:42    mishandled   abc123        4     user_correction,missing_tool_use  "Create a markdown design doc..."
   952|2026-05-19 09:10    prolonged    def456        2     repeated_tool_loop,tool_error     "Fix the test failure..."
   953|2026-05-18 22:31    failed       ghi789        1     action_misrepresentation          "Send the email..."
   954|```
   955|
   956|### 19.6 Inspect one eval unit
   957|
   958|```bash
   959|agent-health show abc123:4
   960|```
   961|
   962|Output sections:
   963|
   964|```text
   965|Status
   966|Primary reason
   967|User request
   968|Assistant response summary
   969|Next user reaction
   970|Tool sequence
   971|Deterministic signals
   972|Barriers
   973|Raw trace pointers
   974|```
   975|
   976|### 19.7 Minimal summary
   977|
   978|Report format can be refined later, but the first summary should exist for sanity checking.
   979|
   980|```bash
   981|agent-health summary --since 7d
   982|```
   983|
   984|Example output:
   985|
   986|```text
   987|Evaluated turns: 118
   988|succeed: 82
   989|failed: 7
   990|mishandled: 18
   991|prolonged: 9
   992|not_evaluable: 2
   993|
   994|Top barriers:
   995|1. user_correction: 11
   996|2. excessive_tool_calls: 8
   997|3. missing_tool_use: 6
   998|4. action_misrepresentation: 3
   999|```
  1000|
  1001|## 20. Batch scheduling
  1002|
  1003|The MVP should support manual runs first. Timed batch can be added with either OS scheduling or a lightweight in-process scheduler.
  1004|
  1005|Recommended order:
  1006|
  1007|1. Manual CLI batch.
  1008|2. OS cron/systemd timer inside the container.
  1009|3. Optional Hermes cron integration later.
  1010|
  1011|Example cron:
  1012|
  1013|```cron
  1014|*/30 * * * * agent-health eval --due --limit 50 >> ~/.hermes/instruction-health/logs/eval.log 2>&1
  1015|```
  1016|
  1017|Avoid using Hermes itself as the first scheduler because the evaluator should not depend on an agent loop to evaluate the agent loop.
  1018|
  1019|## 21. Agent-agnostic adapter layer
  1020|
  1021|Do not build a large generic framework in MVP. Build a small adapter interface around evaluation units.
  1022|
  1023|```python
  1024|from typing import Protocol, Iterable
  1025|
  1026|class AgentAdapter(Protocol):
  1027|    framework_name: str
  1028|
  1029|    def discover_due_sources(self, since: float | None = None) -> Iterable[str]:
  1030|        """Return source ids, e.g. Hermes session ids."""
  1031|
  1032|    def load_source(self, source_id: str) -> dict:
  1033|        """Load raw framework-specific session/run data."""
  1034|
  1035|    def normalize_eval_units(self, raw_source: dict) -> list[dict]:
  1036|        """Return normalized eval-unit dictionaries."""
  1037|
  1038|    def load_trace_events(self, eval_unit_id: str) -> list[dict]:
  1039|        """Return normalized event dictionaries."""
  1040|```
  1041|
  1042|Hermes adapter implementation:
  1043|
  1044|```text
  1045|HermesAdapter
  1046|  source: ~/.hermes/state.db
  1047|  supplemental source: ~/.hermes/instruction-health/events.jsonl
  1048|  source id: session_id
  1049|  eval unit id: hermes:<session_id>:turn:<n>
  1050|```
  1051|
  1052|Future OpenClaw adapter:
  1053|
  1054|```text
  1055|OpenClawAdapter
  1056|  source: to be discovered later
  1057|  source id: OpenClaw conversation/task id
  1058|  eval unit id: openclaw:<source_id>:turn:<n>
  1059|```
  1060|
  1061|The normalized schema should be allowed to evolve. Do not overfit the first design to OpenClaw before OpenClaw’s current log/session internals are verified.
  1062|
  1063|## 22. Privacy and locality
  1064|
  1065|Storage is local by default:
  1066|
  1067|```text
  1068|$HERMES_HOME/instruction-health/
  1069|```
  1070|
  1071|The evaluator should not create cloud resources.
  1072|
  1073|The evaluator should not require Langfuse credentials.
  1074|
  1075|The evaluator should not read or store hidden chain-of-thought fields from Hermes messages.
  1076|
  1077|The evaluator should not send data to a new provider by default. It should use the provider/model already configured for Hermes. However, if the configured Hermes model is remote, judge inputs will still leave the machine. The CLI should print this clearly during `init` and `eval`:
  1078|
  1079|```text
  1080|Judge provider resolved to <provider>/<model>.
  1081|Evaluation data will be sent through the same provider path Hermes uses.
  1082|Use a local/custom provider if you require fully local inference.
  1083|```
  1084|
  1085|No redaction is required in MVP based on current scope, but the design should leave room for a future pre-judge redaction layer.
  1086|
  1087|## 23. Handling incomplete sessions
  1088|
  1089|The system should support incomplete conversations but not build crash forensics in MVP.
  1090|
  1091|Rules:
  1092|
  1093|- If a user request has no assistant response and the session is inactive, create an eval unit with `health_status = not_evaluable` or `failed` depending on evidence.
  1094|- If a turn was interrupted, include `interrupted_or_incomplete` as a barrier.
  1095|- If the process crashed and no trace exists, do not attempt root-cause analysis in MVP.
  1096|- If `on_session_end` says `completed=false` or `interrupted=true`, record that as deterministic evidence.
  1097|
  1098|## 24. Visualisation model
  1099|
  1100|For MVP, visualisation means queryable, structured summaries in CLI output.
  1101|
  1102|The first visualisations should answer:
  1103|
  1104|```text
  1105|Which recent turns were failed, mishandled, or prolonged?
  1106|Why were they classified that way?
  1107|What user reaction suggested the problem?
  1108|What tools were involved?
  1109|Which barriers repeat most often?
  1110|```
  1111|
  1112|Do not build charts before the evaluator is reliable. Once the schema stabilizes, charts can be added from the sidecar DB.
  1113|
  1114|Future visual options:
  1115|
  1116|```text
  1117|local TUI
  1118|small FastAPI/HTML dashboard
  1119|SQLite-backed static report
  1120|Grafana over SQLite/DuckDB export
  1121|```
  1122|
  1123|## 25. Implementation roadmap
  1124|
  1125|### Phase 0 — Hermes data inspection
  1126|
  1127|Deliverables:
  1128|
  1129|- Confirm current Hermes `state.db` schema on a real profile.
  1130|- Write a small script that lists recent sessions and messages.
  1131|- Confirm how tool calls/tool results are represented in actual DB rows.
  1132|- Confirm whether `api_call_count`, `tool_call_count`, and token fields are populated reliably.
  1133|
  1134|Acceptance criteria:
  1135|
  1136|```text
  1137|agent-health inspect hermes --hermes-home ~/.hermes
  1138|```
  1139|
  1140|prints recent sessions, user messages, assistant messages, tool calls, and tool results without using an LLM.
  1141|
  1142|### Phase 1 — Normalized eval units
  1143|
  1144|Deliverables:
  1145|
  1146|- `HermesAdapter`.
  1147|- Eval-unit normalization.
  1148|- Sidecar `evals.db` schema.
  1149|- CLI command to list due eval units.
  1150|
  1151|Acceptance criteria:
  1152|
  1153|```bash
  1154|agent-health import hermes --since 24h
  1155|agent-health units --since 24h
  1156|```
  1157|
  1158|shows one row per user request, including current request, final assistant response presence, next user reaction presence, and tool count.
  1159|
  1160|### Phase 2 — Passive Hermes plugin
  1161|
  1162|Deliverables:
  1163|
  1164|- `~/.hermes/plugins/instruction-health/plugin.yaml`.
  1165|- Hook registration.
  1166|- `events.jsonl` capture.
  1167|- Session dirty marker.
  1168|
  1169|Acceptance criteria:
  1170|
  1171|After a normal Hermes conversation, `events.jsonl` contains session, LLM, and tool events, and Hermes behavior is unchanged.
  1172|
  1173|### Phase 3 — Deterministic signals
  1174|
  1175|Deliverables:
  1176|
  1177|- Tool error detection.
  1178|- Repeated tool detection.
  1179|- Prolonged run detection.
  1180|- User reaction classifier.
  1181|- Completion-claim heuristic.
  1182|
  1183|Acceptance criteria:
  1184|
  1185|```bash
  1186|agent-health signals <eval_unit_id>
  1187|```
  1188|
  1189|shows deterministic evidence without calling an LLM.
  1190|
  1191|### Phase 4 — LLM judge
  1192|
  1193|Deliverables:
  1194|
  1195|- Judge prompt v1.
  1196|- Existing-Hermes-provider connection path.
  1197|- Strict JSON parser and retry.
  1198|- `llm_evals` and `barriers` storage.
  1199|
  1200|Acceptance criteria:
  1201|
  1202|```bash
  1203|agent-health eval --due --limit 10
  1204|agent-health list --status failed,mishandled,prolonged
  1205|```
  1206|
  1207|returns structured statuses and barriers.
  1208|
  1209|### Phase 5 — CLI visualizer
  1210|
  1211|Deliverables:
  1212|
  1213|- `list` command.
  1214|- `show` command.
  1215|- `summary` command.
  1216|- Raw trace pointers back to Hermes session/message ids.
  1217|
  1218|Acceptance criteria:
  1219|
  1220|A Hermes user can identify recent bumpy turns and inspect why they were judged bumpy without opening raw logs manually.
  1221|
  1222|### Phase 6 — Future feedback and improvement leads
  1223|
  1224|Not MVP. Keep this as a future extension.
  1225|
  1226|Possible future additions:
  1227|
  1228|- `agent-health feedback <eval_unit_id>`.
  1229|- User correction stored as first-class evidence.
  1230|- Feedback override of judge status.
  1231|- Re-evaluation after feedback.
  1232|- Candidate lessons learned.
  1233|- Candidate prompt patches.
  1234|- Candidate regression tests.
  1235|- Human approval queue before writing anything back into Hermes memory, skills, or config.
  1236|
  1237|## 26. MVP acceptance criteria
  1238|
  1239|The MVP is successful when all of the following are true.
  1240|
  1241|1. It installs as a Hermes plugin.
  1242|2. It can run inside the same container/profile as Hermes.
  1243|3. It uses Hermes `state.db` as the primary source of truth.
  1244|4. It captures supplemental hook events without changing agent behavior.
  1245|5. It normalizes a conversation into per-user-request eval units.
  1246|6. It evaluates every due user request.
  1247|7. It uses the next user message as reaction evidence when available.
  1248|8. It classifies turns into `succeed`, `failed`, `mishandled`, `prolonged`, or `not_evaluable`.
  1249|9. It stores strict JSON eval outputs in local SQLite.
  1250|10. It lists failed, mishandled, and prolonged turns from the CLI.
  1251|11. It does not require Langfuse.
  1252|12. It does not require a separate evaluator API key.
  1253|13. It does not store hidden reasoning fields in normalized eval records.
  1254|14. It remains useful even if the LLM judge is unavailable by showing deterministic signals.
  1255|
  1256|## 27. Main risks and mitigations
  1257|
  1258|### Risk: Hermes schema changes
  1259|
  1260|Mitigation:
  1261|
  1262|- Prefer importing Hermes `SessionDB` APIs if available.
  1263|- Fall back to raw SQL only where needed.
  1264|- Store detected Hermes schema version.
  1265|- Add `agent-health doctor` to warn on unsupported schemas.
  1266|
  1267|### Risk: Judge is biased because it uses the same model as the agent
  1268|
  1269|Mitigation:
  1270|
  1271|- Accept this for MVP because avoiding another API is a core goal.
  1272|- Store judge model/provider with every eval.
  1273|- Allow optional separate judge config later.
  1274|- Preserve deterministic signals so users can audit the judge.
  1275|
  1276|### Risk: User reaction is ambiguous
  1277|
  1278|Mitigation:
  1279|
  1280|- Use reaction as evidence, not absolute truth.
  1281|- Let the judge classify scope changes separately from corrections.
  1282|- Use `confidence = low` when uncertain.
  1283|
  1284|### Risk: Too much data sent to judge
  1285|
  1286|Mitigation:
  1287|
  1288|- Cap tool outputs.
  1289|- Summarize traces deterministically.
  1290|- Include only previous context needed for the current user request.
  1291|- Exclude hidden reasoning fields.
  1292|
  1293|### Risk: Hooks slow down Hermes
  1294|
  1295|Mitigation:
  1296|
  1297|- Append short records only.
  1298|- No LLM calls in hooks.
  1299|- No heavy DB reads in hooks.
  1300|- Catch all hook exceptions.
  1301|
  1302|### Risk: The evaluator becomes too broad
  1303|
  1304|Mitigation:
  1305|
  1306|- Keep self-improvement and user feedback as future extensions.
  1307|- Keep reporting minimal until eval schema works.
  1308|- Keep OpenClaw support as Phase 2.
  1309|
  1310|## 28. Concrete first implementation tasks
  1311|
  1312|1. Create repo skeleton.
  1313|2. Add Python package with CLI entry point `agent-health` as placeholder.
  1314|3. Add config loader for `$HERMES_HOME/instruction-health/config.yaml`.
  1315|4. Implement `HermesStateReader` that reads `sessions` and `messages` from `state.db`.
  1316|5. Implement `inspect hermes` command.
  1317|6. Implement `HermesAdapter.normalize_eval_units()`.
  1318|7. Create sidecar `evals.db` and migrations.
  1319|8. Implement deterministic signal extractor.
  1320|9. Implement Hermes plugin event capture.
  1321|10. Implement `events.jsonl` reader and event joiner.
  1322|11. Implement trace summary builder.
  1323|12. Implement judge prompt v1.
  1324|13. Implement Hermes-provider judge client.
  1325|14. Implement JSON parsing and retry.
  1326|15. Implement `eval --due`.
  1327|16. Implement `list`, `show`, and `summary` commands.
  1328|17. Add fixtures with synthetic Hermes DB rows.
  1329|18. Add tests for multi-turn reaction handling.
  1330|19. Add tests for repeated tool loops.
  1331|20. Add tests for action misrepresentation.
  1332|
  1333|## 29. Suggested repository structure
  1334|
  1335|```text
  1336|.
  1337|├── pyproject.toml
  1338|├── README.md
  1339|├── docs/
  1340|│   └── design.md
  1341|├── src/
  1342|│   └── agent_health/
  1343|│       ├── __init__.py
  1344|│       ├── cli.py
  1345|│       ├── config.py
  1346|│       ├── db.py
  1347|│       ├── adapters/
  1348|│       │   ├── __init__.py
  1349|│       │   ├── base.py
  1350|│       │   └── hermes.py
  1351|│       ├── hermes_plugin/
  1352|│       │   ├── plugin.yaml
  1353|│       │   └── __init__.py
  1354|│       ├── normalize.py
  1355|│       ├── signals.py
  1356|│       ├── reactions.py
  1357|│       ├── trace_summary.py
  1358|│       ├── judge.py
  1359|│       ├── prompts/
  1360|│       │   └── instruction_health_v1.txt
  1361|│       └── visualize.py
  1362|├── tests/
  1363|│   ├── fixtures/
  1364|│   │   └── hermes_state_sample.db
  1365|│   ├── test_hermes_reader.py
  1366|│   ├── test_normalize.py
  1367|│   ├── test_signals.py
  1368|│   └── test_judge_schema.py
  1369|└── examples/
  1370|    └── config.yaml
  1371|```
  1372|
  1373|## 30. Open questions to resolve during implementation
  1374|
  1375|These should be answered by inspecting a real Hermes installation and writing the first importer.
  1376|
  1377|1. Does Hermes always write tool result messages with stable `tool_call_id` and `tool_name`?
  1378|2. Are `api_call_count` and `tool_call_count` reliable per session for all surfaces?
  1379|3. Does `post_llm_call` fire after every user turn in the same way for CLI and gateway sessions?
  1380|4. How should background sessions be represented in the eval unit id?
  1381|5. Can the evaluator cleanly import Hermes runtime provider helpers from a plugin/CLI package without depending on internal unstable imports?
  1382|6. What is the safest way to resolve `HERMES_HOME` for profile aliases?
  1383|7. Should long-running coding tasks have different prolonged thresholds than short chat tasks?
  1384|8. Should the first visualizer show session-level rollups or only turn-level rows?
  1385|
  1386|## 31. References consulted
  1387|
  1388|- Hermes Event Hooks: https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks
  1389|- Hermes Build a Plugin guide: https://hermes-agent.nousresearch.com/docs/guides/build-a-hermes-plugin
  1390|- Hermes Built-in Plugins: https://hermes-agent.nousresearch.com/docs/user-guide/features/built-in-plugins
  1391|- Hermes Session Storage: https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage
  1392|- Hermes Sessions user guide: https://hermes-agent.nousresearch.com/docs/user-guide/sessions
  1393|- Hermes Provider Runtime Resolution: https://hermes-agent.nousresearch.com/docs/developer-guide/provider-runtime
  1394|- Hermes Programmatic Integration: https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration
  1395|