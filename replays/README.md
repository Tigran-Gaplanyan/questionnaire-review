# Saved real model responses

`replays/<purpose>/<first 24 hex chars of the request hash>.json`, where purpose is
`draft_answer` or `verify_answer`. Each file contains:

- `provenance: "recorded-live-response"`, `recorded_at` and the SDK version
- `request`: the exact Messages API request (model, system prompt, documents and question,
  JSON schema, effort, fallback settings)
- `response`: the unedited API response (`message.to_dict()`), including the response id and
  the model that served it

Files are written only by live calls (`--mode live` or `--mode auto`). Simulated failures are
never written here. Replay looks a file up by the hash of the request the app is about to send,
so any change to a prompt, a document or a setting is a replay miss, and a miss is reported as a
failure rather than guessed.
