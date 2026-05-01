# Human Gate Phase 3 Design Blueprint

Status: Draft for implementation planning
Owner: Chat + Runtime + Human Gate
Scope: Remote-user reply collection during Human Gate pauses

## 1. Goal

Implement multi-user Human Gate reply collection for configured remote users during an active gate pause, while preserving existing leader SSE run flow and Redis-backed coordination patterns.

## 2. Decision Summary

1. Keep leader run streaming on existing SSE.
2. Add WebSocket channel for remote users (bidirectional updates + submit replies).
3. Keep Redis as the phase-3 runtime state authority.
4. Use Redis Pub/Sub for fanout notifications only, not as the source of truth.
5. Do not add Kafka in Phase 3.

Rationale:
- Leader flow is stable and already integrated with run lifecycle in server/views.py.
- Remote participants need realtime two-way interaction; WebSocket is the correct transport.
- Pub/Sub alone is lossy; quorum correctness needs durable in-progress state.
- Kafka adds operational complexity without immediate benefit in a single-service deployment.

## 3. Non-goals for Phase 3

1. No replacement of leader SSE with WebSocket.
2. No new persistent storage for token/presence/reply transient state beyond Redis.
3. No AutoGen participant injection for remote users (still runtime overlay only).
4. No distributed event replay pipeline (Kafka deferred).

## 4. Existing Contracts to Preserve

1. Pre-run readiness gate remains before MCP OAuth gate.
2. Session leader remains the only actor allowed to start runs and authorize MCP.
3. Remote users still authenticate via invitation token only.
4. Session delete continues purging remote-user Redis keys.
5. Secrets/tokens/URLs never logged or traced.

## 5. Phase 3 Architecture

### 5.1 Leader channel

- Existing SSE run stream continues to deliver assistant events and gate event.
- During gate pause, leader UI shows quorum progress panel.
- Leader view can use polling endpoint (phase-3 minimal) or optional SSE reconnect strategy.

Recommendation for first implementation:
- Use polling endpoint for leader gate progress (same cadence as readiness, 3 seconds) to avoid changing SSE lifecycle.

### 5.2 Remote-user channel

- New remote-user chat surface connects via WebSocket.
- Remote user receives:
  - gate opened for round N
  - required/optional status
  - quorum progress updates
  - gate closed/unblocked signal
- Remote user can submit exactly one reply per gate round (idempotent by user_id + gate_round).

### 5.3 Redis role separation

- Durable transient state keys store gate context + replies.
- Pub/Sub channels broadcast updates to connected WebSocket consumers.
- If Pub/Sub message is missed, consumers reconstruct from durable keys using snapshot endpoint.

## 6. Data Model (Redis)

Namespace prefix: {ns} = REDIS_NAMESPACE

### 6.1 Gate context key

Key:
- {ns}:remote_gate:{session_id}:{gate_round}:context

Value (JSON):
- session_id
- gate_round
- quorum_mode: yes | first_win | team_config
- required_user_ids: list[str]
- opened_at_iso
- opened_by: leader
- status: open | satisfied | cancelled | expired

TTL:
- REMOTE_GATE_CONTEXT_TTL_SECONDS (default 12h)

### 6.2 Reply map key

Key:
- {ns}:remote_gate:{session_id}:{gate_round}:replies

Type:
- Redis hash

Field:
- user_id

Value (JSON):
- user_id
- user_name
- text
- attachment_ids: list[str]
- submitted_at_iso

TTL:
- matches context TTL

### 6.3 Quorum snapshot key (optional optimization)

Key:
- {ns}:remote_gate:{session_id}:{gate_round}:snapshot

Value (JSON):
- required_user_ids
- replied_user_ids
- pending_user_ids
- satisfied: bool
- resolved_by: quorum | leader_stop | timeout

TTL:
- matches context TTL

### 6.4 Event channel (fanout only)

Channel:
- {ns}:remote_gate:{session_id}:{gate_round}:events

Events:
- gate_opened
- reply_recorded
- quorum_satisfied
- gate_closed

Important:
- Pub/Sub is advisory fanout only. Correctness always re-checks hash/context keys.

## 7. MongoDB additions

Use minimal persistent additions for reload visibility and audit of run progression:

chat_sessions fields:
1. pending_remote_gate (optional object)
- gate_round
- quorum_mode
- required_user_ids
- satisfied (bool)
- updated_at (BSON Date)

2. status handling
- Option A (minimal change): keep status=awaiting_input and infer remote gate from pending_remote_gate
- Option B (clear UI semantics): add awaiting_remote_replies

Recommendation:
- Start with Option A to reduce status-enum blast radius.
- Add Option B only if UI/ops observability needs explicit status separation.

## 8. Quorum evaluation rules

Input:
- quorum_mode
- required_user_ids
- replies hash keys
- team_config target set (when applicable)

Rules:
1. yes
- satisfied when every required_user_id has a reply for gate_round.
2. first_win
- satisfied when first required_user reply arrives.
3. team_config
- selector output defines target_user_ids for this gate round.
- satisfied when target_user_ids are all present.

Edge behavior:
1. Duplicate submission by same user updates existing hash field (latest write wins) and republishes reply_recorded.
2. Reply from non-required user is ignored for quorum but may be stored for display if desired.
3. If required set becomes empty, gate is immediately satisfied.

## 9. API and WS contracts

### 9.1 HTTP endpoints (leader)

1. GET /chat/sessions/<id>/remote-gate/status/
- Returns gate snapshot for current gate round:
  - round, quorum_mode, required, replied, pending, satisfied

2. POST /chat/sessions/<id>/remote-gate/cancel/
- Leader aborts gate and sets session to stopped (or idle, policy-defined).

3. Optional: POST /chat/sessions/<id>/remote-gate/continue/
- Leader explicit continue when satisfied (if auto-continue is not used).

All leader endpoints remain X-App-Secret-Key protected.

### 9.2 Remote join and WS

1. GET /chat/<session_id>/remote_user/<token>/
- Serves remote-user page after token lookup.

2. WS /ws/chat/<session_id>/remote-user/<token>/

Client -> server messages:
1. heartbeat
2. submit_reply
- text
- attachment_ids (optional)
- gate_round

Server -> client messages:
1. gate_state
2. quorum_progress
3. gate_closed
4. error

Authentication:
- token resolved via Redis reverse lookup.
- no APP secret for remote users.

## 10. Runtime flow

1. Run reaches Human Gate pause.
2. Backend opens remote gate context for gate_round.
3. Backend emits gate info to leader response path and remote WS subscribers.
4. Remote replies accumulate in Redis hash.
5. Quorum evaluator runs on each submission.
6. On satisfied:
- lock resolution (single winner)
- append remote replies as discussions role=user entries
- clear pending_remote_gate
- transition to idle
- continue next run invocation with synthesized task context

Idempotency guard:
- setnx lock key {ns}:remote_gate:{session_id}:{gate_round}:resolve_lock with short TTL to avoid double-resume across workers.

## 11. Observability and safety

### 11.1 Log events (structured)

1. agents.remote_gate.opened
2. agents.remote_gate.reply_recorded
3. agents.remote_gate.quorum_satisfied
4. agents.remote_gate.closed

Allowed extras:
- session_id
- gate_round
- user_id
- required_count
- replied_count
- pending_count

Forbidden in logs/spans:
- token
- join_url
- raw authorization-like values

### 11.2 OTel spans

1. service.remote_gate.open
2. service.remote_gate.submit_reply
3. service.remote_gate.evaluate_quorum
4. service.remote_gate.resolve

Payload handling must use set_payload_attribute redaction path.

## 12. Failure semantics

1. Redis unavailable during gate open/submit/evaluate
- fail fast with user-visible error
- do not silently continue without quorum checks

2. WS disconnect
- remote user marked offline by heartbeat TTL expiry
- state remains reconstructable from Redis keys

3. Leader refresh
- UI restores from chat session + remote-gate status endpoint

4. Duplicate tabs
- replies keyed by user_id keep behavior deterministic

## 13. Security model

1. Invitation token remains opaque random string.
2. Remote token grants only session-scoped gate interaction.
3. Remote token does not allow config edits, run start, MCP auth, or secret-backed endpoints.
4. Token TTL and revocation follow existing Redis policy.
5. Session delete purges phase-3 keys in same cleanup sequence.

## 14. Sub-phase implementation plan

### Phase 3A: Data + contracts

1. Add Redis remote-gate key helpers in agents/session_coordination.py.
2. Add services facade methods in server/services.py for:
- open_remote_gate
- submit_remote_gate_reply
- get_remote_gate_status
- evaluate_remote_gate_quorum
- resolve_remote_gate
3. Add chat_sessions pending_remote_gate normalization and render support.
4. Add docs updates for API and storage map.

Exit criteria:
- Unit-style service checks (manual for now) demonstrate quorum evaluation for all 3 modes.

### Phase 3B: Remote transport

1. Add remote-user HTML page endpoint by token.
2. Add WebSocket consumer + routing.
3. Add heartbeat + presence refresh integration.
4. Add submit_reply message handling.
5. Add Pub/Sub fanout for live progress updates.

Exit criteria:
- Two browsers can observe same gate state changes in near-realtime.

### Phase 3C: Leader UI integration

1. Add gate-progress panel in home.js during awaiting_input when pending_remote_gate exists.
2. Poll remote-gate status every 3s.
3. Show required/replied/pending counts and per-user state.
4. Wire cancel behavior.
5. Auto-continue or enable continue when satisfied (policy decision).

Exit criteria:
- Leader can observe and manage gate lifecycle without page reload.

### Phase 3D: Resume integration

1. On quorum satisfied, append remote replies to discussions[].
2. Trigger continue flow into next run.
3. Ensure single-assistant mode behavior remains consistent.
4. Ensure stop/cancel semantics remain deterministic.

Exit criteria:
- End-to-end run pause -> remote replies -> continue works across restarts and multi-worker setups.

## 15. Open decisions (for your step-by-step direction)

1. Status model
- Keep awaiting_input + pending_remote_gate, or add awaiting_remote_replies?

2. Continue policy
- Auto-continue when satisfied, or require leader click?

3. Reply mutability
- Latest reply overwrites prior reply, or first reply immutable?

4. team_config target computation
- At gate open only, or recomputed on each reply?

5. Attachment support for remote replies in Phase 3
- Include now or defer to Phase 3.1?

## 16. Suggested first implementation defaults

1. Keep status=awaiting_input.
2. Auto-continue when quorum satisfied.
3. Latest reply wins per user.
4. team_config targets computed once at gate open.
5. Text-only replies first; attachments deferred one sub-phase.

These defaults minimize code surface and align with current flow.
