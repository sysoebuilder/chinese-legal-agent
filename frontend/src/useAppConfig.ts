import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  getAppConfig,
  patchModels,
  patchMouldes,
  patchSecrets,
  patchPermissions,
} from "./api";
import type {
  AppConfig,
  ModelEntry,
  MouldesPatch,
  PermissionsConfig,
} from "./types";

export function useAppConfig() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [needsAuth, setNeedsAuth] = useState(false);
  const mounted = useRef(false);
  const pending = useRef<AbortController | null>(null);
  const locked = useRef(false);

  function report(reason: unknown, prefix = "") {
    if (!mounted.current) return;
    const auth = reason instanceof ApiError && reason.status === 401;
    setNeedsAuth(auth);
    setError(
      prefix +
        (auth
          ? "请先填写 API 访问令牌。"
          : reason instanceof Error
            ? reason.message
            : "配置暂时不可用，请重试。"),
    );
  }

  async function reload(): Promise<boolean> {
    if (locked.current) return false;
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    setLoading(true);
    setError("");
    try {
      const next = await getAppConfig(controller.signal);
      if (!mounted.current || controller.signal.aborted) return false;
      setConfig(next);
      setNeedsAuth(false);
      return true;
    } catch (reason) {
      if (!controller.signal.aborted) report(reason);
      return false;
    } finally {
      if (mounted.current && pending.current === controller) setLoading(false);
    }
  }

  async function mutate(
    name: string,
    operation: () => Promise<void>,
  ): Promise<boolean> {
    if (locked.current) return false;
    locked.current = true;
    pending.current?.abort();
    setLoading(false);
    setSaving(name);
    setError("");
    setNotice("");
    try {
      await operation();
      if (mounted.current) setNeedsAuth(false);
      return true;
    } catch (reason) {
      report(reason);
      return false;
    } finally {
      locked.current = false;
      if (mounted.current) setSaving(null);
    }
  }

  async function selectModel(name: string) {
    return mutate("switch", async () => {
      const next = await patchModels({ active: name });
      if (mounted.current) {
        setConfig((previous) => previous && { ...previous, modelconfig: next });
        setNotice("模型已切换，下一次发送时使用。");
      }
    });
  }

  async function saveModel(
    name: string,
    entry: ModelEntry,
    apiKey: string,
    activate: boolean,
  ) {
    return mutate("model", async () => {
      let keySaved = false;
      if (apiKey.trim()) {
        const secrets = await patchSecrets({
          [entry.api_key_ref]: apiKey.trim(),
        });
        keySaved = true;
        if (mounted.current)
          setConfig(
            (previous) => previous && { ...previous, secretsconfig: secrets },
          );
      }
      try {
        const models = await patchModels({
          models: { [name]: entry },
          ...(activate ? { active: name } : {}),
        });
        if (mounted.current) {
          setConfig(
            (previous) => previous && { ...previous, modelconfig: models },
          );
          setNotice("模型配置已保存。");
        }
      } catch (reason) {
        if (keySaved)
          throw new Error(
            "密钥已保存，模型设置未保存，请重试。" +
              (reason instanceof Error ? reason.message : ""),
          );
        throw reason;
      }
    });
  }

  async function savePlugins(
    patch: MouldesPatch,
    notice = "插件选择已保存，后续运行生效。",
  ) {
    return mutate("plugins", async () => {
      const next = await patchMouldes(patch);
      if (mounted.current) {
        setConfig(
          (previous) => previous && { ...previous, mouldesconfig: next },
        );
        setNotice(notice);
      }
    });
  }

  async function saveSecrets(keys: Record<string, string | null>) {
    return mutate("secrets", async () => {
      const next = await patchSecrets(keys);
      if (mounted.current) {
        setConfig(
          (previous) => previous && { ...previous, secretsconfig: next },
        );
        setNotice("凭据已保存，后续运行生效。");
      }
    });
  }

  async function savePermissions(mode: PermissionsConfig["mode"]) {
    return mutate("permissions", async () => {
      const next = await patchPermissions({ mode });
      if (mounted.current) {
        setConfig(
          (previous) => previous && { ...previous, permissionsconfig: next },
        );
        setNotice("Agent 权限已保存，后续运行生效。");
      }
    });
  }

  useEffect(() => {
    mounted.current = true;
    void reload();
    return () => {
      mounted.current = false;
      pending.current?.abort();
    };
  }, []);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 4000);
    return () => clearTimeout(timer);
  }, [notice]);

  return {
    config,
    loading,
    saving,
    error,
    notice,
    needsAuth,
    reload,
    selectModel,
    saveModel,
    savePlugins,
    saveSecrets,
    savePermissions,
    dismissError: () => setError(""),
  };
}

export type AppConfigController = ReturnType<typeof useAppConfig>;
