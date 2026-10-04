"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { Profile, ProfileOperation, ResumeResponse } from "@/types/api";

export function useProfile() {
  const [data, setData] = useState<ResumeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const reload = useCallback(async () => {
    try {
      setData(await api<ResumeResponse>("/resume"));
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    api<ResumeResponse>("/resume")
      .then((d) => alive && setData(d))
      .catch((e: Error) => toast.error(e.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const apply = useCallback(async (operations: ProfileOperation[], success?: string) => {
    setSaving(true);
    try {
      const r = await api<{ profile: Profile }>("/resume", { method: "PATCH", json: { operations } });
      setData((d) => (d ? { ...d, profile: r.profile } : d));
      if (success) toast.success(success);
      return r.profile;
    } catch (e) {
      toast.error((e as Error).message);
      throw e;
    } finally {
      setSaving(false);
    }
  }, []);

  return { data, profile: data?.profile ?? null, loading, saving, reload, apply, setData };
}
