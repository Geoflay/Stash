import { FolderOpen, ListX, Play, RotateCcw, Scissors, Share2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { formatDuration, formatPercent } from "@/lib/format";
import { openFile } from "@/features/edit/store";
import { isAndroid } from "@/lib/platform";
import { EditableTitle } from "@/components/editable-title";
import { clearFinished, isActive, jobAction, renameJob, retryJob, useJobs, type Job } from "./store";

function Status({ job }: { job: Job }) {
  const { t } = useTranslation();
  if (job.step === "failed" && job.error) {
    const message = t(job.error.key, job.error.params);
    // A technical detail the message leaves out (FFmpeg's) goes underneath, for a bug report.
    const detail = job.error.params.detail;
    const extra = typeof detail === "string" && detail && !message.includes(detail) ? detail : null;
    return (
      <div className="grid gap-0.5">
        <p className="text-xs text-destructive">{message}</p>
        {extra && <p className="text-xs text-muted-foreground wrap-anywhere select-text">{extra}</p>}
      </div>
    );
  }
  // A finished job's note: the file came out over the size limit.
  if (job.step === "done" && job.error) {
    return <p className="text-xs text-muted-foreground">{t(job.error.key, job.error.params)}</p>;
  }
  let text: string = t(`jobs.${job.step}`);
  if (job.step === "download" && job.fraction !== null) {
    text = t("jobs.downloading", { percent: formatPercent(job.fraction) });
    if (job.eta !== null) text += ` · ${t("jobs.eta", { time: formatDuration(job.eta) })}`;
  }
  if (job.step === "encode" && job.fraction !== null) {
    text = t("jobs.encoding", { percent: formatPercent(job.fraction) });
  }
  return <p className="truncate text-xs text-muted-foreground">{text}</p>;
}

function JobItem({ job }: { job: Job }) {
  const { t } = useTranslation();
  const active = isActive(job);
  const path = job.path;
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5">
      <div className="grid min-w-48 flex-1 gap-1.5">
        {/* Android can't rename a folder of photos. */}
        {isAndroid && !job.editable ? (
          <p className="truncate text-sm font-medium" title={job.title}>
            {job.title}
          </p>
        ) : (
          <EditableTitle value={job.title} onRename={(title) => void renameJob(job, title)} className="text-sm" />
        )}
        {active && <Progress value={(job.fraction ?? 0) * 100} className="h-1" />}
        <Status job={job} />
      </div>
      {active && (
        <Button variant="ghost" size="icon" aria-label={t("jobs.cancel")} onClick={() => jobAction("cancel_download", { id: job.id })}>
          <X className="text-foreground" />
        </Button>
      )}
      {job.step === "failed" && job.start && (
        <Button variant="ghost" onClick={() => void retryJob(job)}>
          <RotateCcw className="text-foreground" />
          {t("jobs.retry")}
        </Button>
      )}
      {job.step === "done" && path && (
        <div className="flex gap-1">
          <Button variant="ghost" onClick={() => jobAction("open_file", { path })}>
            <Play className="text-foreground" />
            {t("jobs.open")}
          </Button>
          {isAndroid ? (
            <Button variant="ghost" onClick={() => jobAction("share_file", { path })}>
              <Share2 className="text-foreground" />
              {t("jobs.share")}
            </Button>
          ) : (
            <Button variant="ghost" onClick={() => jobAction("reveal_file", { path })}>
              <FolderOpen className="text-foreground" />
              {t("jobs.reveal")}
            </Button>
          )}
          {job.editable && (
            <Button variant="ghost" onClick={() => void openFile(path)}>
              <Scissors className="text-foreground" />
              {t("jobs.edit")}
            </Button>
          )}
        </div>
      )}
    </li>
  );
}

export function JobList() {
  const { t } = useTranslation();
  const jobs = useJobs((s) => s.jobs);
  return (
    <section className="mx-auto grid w-full max-w-3xl content-start gap-3">
      <h2 className="sr-only">{t("download.list")}</h2>
      {jobs.some((job) => !isActive(job)) && (
        <Button variant="ghost" size="sm" className="justify-self-end" onClick={clearFinished}>
          <ListX className="text-foreground" />
          {t("jobs.clear")}
        </Button>
      )}
      {jobs.length ? (
        <ul className="divide-y rounded-xl border bg-card">
          {jobs.map((job) => (
            <JobItem key={job.id} job={job} />
          ))}
        </ul>
      ) : (
        <p className="rounded-xl border p-4 text-sm text-muted-foreground">{t("download.listEmpty")}</p>
      )}
    </section>
  );
}
