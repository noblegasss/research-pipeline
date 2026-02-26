"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export default function Home() {
  const { t } = useI18n();
  const router = useRouter();

  useEffect(() => {
    api.listRuns().then((runs) => {
      if (runs.length > 0) {
        router.replace(`/runs/${runs[0].run_date}`);
      } else {
        router.replace("/settings");
      }
    }).catch(() => {
      router.replace("/settings");
    });
  }, [router]);

  return (
    <div className="flex items-center justify-center h-64 text-gray-400 text-sm">
      {t("loading")}
    </div>
  );
}
