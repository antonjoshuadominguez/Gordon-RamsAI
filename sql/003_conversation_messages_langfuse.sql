-- Optional: persist Langfuse trace id per assistant message (for ratings after reload)
alter table public.conversation_messages
  add column if not exists langfuse_trace_id text;
