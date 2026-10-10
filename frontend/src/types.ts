export type RunStatus =
  | "queued"
  | "running"
  | "waiting_approval"
  | "waiting_input"
  | "succeeded"
  | "failed"
  | "cancelled";
export interface Session {
  thread_id: string;
  name: string;
  created_at: string;
  updated_at: string;
}
export interface SessionPage {
  sessions: Session[];
  has_more: boolean;
  next_offset: number | null;
}
export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  reasoning?: string;
}
export interface HistoryPage {
  thread_id: string;
  messages: Message[];
  has_more: boolean;
  next_before: string | null;
}
export interface ToolAction {
  name: string;
  args: Record<string, unknown>;
}
export interface PendingInterrupt {
  id: string;
  value: { type?: string; question?: string; action_requests?: ToolAction[] };
}
export type Responses = Record<
  string,
  { answer: string } | { decisions: { type: "approve" | "reject" }[] }
>;
export interface Run {
  run_id: string;
  thread_id: string;
  status: RunStatus;
  provider: string;
  model: string;
  cancel_requested: boolean;
  interrupts: PendingInterrupt[];
  answer: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  generation: number;
  revision: number;
}
export interface Recovery {
  run: Run | null;
  can_reconnect: boolean;
  busy: boolean;
  pending_nodes: string[];
}
export interface StreamEvent {
  event: string;
  data: Partial<Run> & {
    text?: string;
    message_id?: string;
    node?: string;
    generation?: number;
    status?: RunStatus;
    interrupts?: PendingInterrupt[];
    answer?: string;
    error?: string;
    tool_name?: string;
  };
}
export interface LiveMessage {
  content: string;
  reasoning: string;
  status: string;
}

export interface ModelEntry {
  provider: "glm" | "deepseek";
  model: string;
  base_url: string;
  api_key_ref: string;
}
export interface ModelsConfig {
  active: string;
  models: Record<string, ModelEntry>;
}
export interface PluginConfig {
  kind: "builtin" | "http" | "stdio";
  able: boolean;
  url: string | null;
  headers: Record<string, string>;
  command: string | null;
  args: string[];
  env: Record<string, string>;
  cwd: string | null;
  read_timeout_seconds: number;
  trust_env: boolean;
}
export interface MouldesConfig {
  legal: { able: boolean };
  plugins: Record<string, PluginConfig>;
}
export interface SecretsConfig {
  keys: Record<string, boolean>;
}
export interface PermissionsConfig {
  mode: "restricted" | "unrestricted";
}
export interface AppConfig {
  modelconfig: ModelsConfig;
  mouldesconfig: MouldesConfig;
  secretsconfig: SecretsConfig;
  permissionsconfig: PermissionsConfig;
}
export interface ModelsPatch {
  active?: string;
  models?: Record<string, ModelEntry>;
}
export interface MouldesPatch {
  legal?: { able: boolean };
  plugins?: Record<string, Partial<PluginConfig>>;
}
