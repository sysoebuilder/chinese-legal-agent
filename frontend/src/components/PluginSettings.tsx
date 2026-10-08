import { useState } from "react";
import { IconScale } from "../icons";
import { Toggle } from "./Toggle";
import type { AppConfigController } from "../useAppConfig";

const labels: Record<string, [string, string]> = {
  current_time: ["当前时间", "获取日期、时间与时区。"],
  web_search: ["网络搜索", "搜索公开网页，获取最新信息。"],
  search_memory: ["搜索记忆", "从过去的对话中找回相关信息。"],
  list_memories: ["查看记忆", "列出已保存的长期记忆。"],
  update_memory: ["更新记忆", "修改已保存的记忆内容。"],
  delete_memory: ["删除记忆", "删除不再需要的长期记忆。"],
  send_email: ["发送邮件", "通过已配置的邮箱发送邮件。"],
  run_command: ["本机终端", "在 Windows 上执行命令。"],
  github: ["GitHub", "访问代码仓库与协作工具。"],
  cloudflare_docs: ["Cloudflare 文档", "搜索 Cloudflare 开发文档。"],
  windows_terminal: ["Windows 远程终端", "通过已连接的服务执行终端命令。"],
};

export function PluginSettings({
  settings,
}: {
  settings: AppConfigController;
}) {
  const config = settings.config!.mouldesconfig;
  const [legal, setLegal] = useState(config.legal.able);
  const [enabled, setEnabled] = useState(() =>
    Object.fromEntries(
      Object.entries(config.plugins).map(([id, plugin]) => [id, plugin.able]),
    ),
  );
  const busy = !!settings.saving || settings.loading;
  const changed =
    legal !== config.legal.able ||
    Object.entries(enabled).some(
      ([id, able]) => able !== config.plugins[id]?.able,
    );
  const count = Object.values(enabled).filter(Boolean).length;
  return (
    <form
      className="plugin-settings"
      onSubmit={async (event) => {
        event.preventDefault();
        if (busy || !changed) return;
        const plugins = Object.fromEntries(
          Object.entries(enabled)
            .filter(([id, able]) => able !== config.plugins[id]?.able)
            .map(([id, able]) => [id, { able }]),
        );
        await settings.savePlugins({
          ...(legal !== config.legal.able ? { legal: { able: legal } } : {}),
          ...(Object.keys(plugins).length ? { plugins } : {}),
        });
      }}
    >
      <div className="legal-setting">
        <div className="legal-icon">
          <IconScale />
        </div>
        <div className="setting-row-copy">
          <h3>法律子图</h3>
          <p>法律问题进入专门的分析与法规检索流程。</p>
        </div>
        <Toggle
          label="启用法律子图"
          checked={legal}
          disabled={busy}
          onChange={setLegal}
        />
      </div>
      {(["builtin", "mcp"] as const).map((group) => {
        const plugins = Object.entries(config.plugins).filter(
          ([, plugin]) => (plugin.kind === "builtin") === (group === "builtin"),
        );
        if (!plugins.length) return null;
        return (
          <section
            className="plugin-group"
            key={group}
            aria-label={group === "builtin" ? "内置工具" : "连接的插件"}
          >
            <div className="setting-section-label">
              {group === "builtin" ? "内置工具" : "连接的插件"}
              <span>{plugins.length}</span>
            </div>
            {plugins.map(([id, plugin]) => {
              const [title, description] = labels[id] || [
                id,
                "使用已配置的外部工具服务。",
              ];
              const unconfigured = plugin.kind === "http" && !plugin.url;
              return (
                <div className="setting-row" key={id}>
                  <div className="setting-row-copy">
                    <h3>{title}</h3>
                    <p>
                      {unconfigured
                        ? "尚未配置服务地址，配置后可启用。"
                        : description}
                    </p>
                  </div>
                  <Toggle
                    label={`启用${title}`}
                    checked={enabled[id] ?? false}
                    disabled={busy || unconfigured}
                    onChange={(able) =>
                      setEnabled((previous) => ({ ...previous, [id]: able }))
                    }
                  />
                </div>
              );
            })}
          </section>
        );
      })}
      <div className="settings-form-actions plugin-save">
        <span>已选择 {count} 个工具与插件</span>
        <button
          className="primary-button"
          type="submit"
          disabled={busy || !changed}
        >
          {settings.saving === "plugins" ? "保存中…" : "保存选择"}
        </button>
      </div>
    </form>
  );
}
