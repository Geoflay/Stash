import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Copy, Download, ListVideo, Minus, Scissors, Settings as SettingsIcon, Square, X } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DownloadTab } from "@/features/download/DownloadTab";
import { JobList } from "@/features/download/JobList";
import { EditTab } from "@/features/edit/EditTab";
import { useEdit, useFileDrop, type Tab } from "@/features/edit/store";
import { SettingsDialog } from "@/features/settings/SettingsDialog";
import { analyze, isActive, useDownloads, useJobEvents, useJobs } from "@/features/download/store";
import { isAndroid } from "@/lib/platform";
import { cn } from "@/lib/utils";
import { changeSettings, loadSettings, useSettings, type Settings } from "@/features/settings/settings";

/** Applies the theme to <html>, following the system when asked. */
function useTheme(theme: Settings["theme"] | undefined) {
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const dark = theme === "dark" || (theme !== "light" && media.matches);
      document.documentElement.classList.toggle("dark", dark);
      if (isAndroid) invoke("system_bars", { dark }).catch(() => {});
    };
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);
}

/** Android: a link shared from another app (Share → Stash) is analyzed in the Download tab. */
function useShares() {
  useEffect(() => {
    if (!isAndroid) return;
    const take = async () => {
      const text = await invoke<string | null>("take_shared").catch(() => null);
      const url = text?.match(/https?:\/\/\S+/)?.[0];
      if (!url) return;
      useEdit.setState({ tab: "download" });
      useDownloads.setState({ url });
      await analyze();
    };
    // The share that opened the app, then those that come while it runs.
    void take();
    const unlisten = listen("shared", () => void take());
    return () => void unlisten.then((stop) => stop());
  }, []);
}

/** Windows: minimize, maximize and close, in place of the system title bar. */
function WindowControls() {
  const { t } = useTranslation();
  const [maximized, setMaximized] = useState(false);
  useEffect(() => {
    const check = () => void getCurrentWindow().isMaximized().then((m) => setMaximized(Boolean(m)), () => {});
    check();
    const unlisten = getCurrentWindow().onResized(check);
    return () => void unlisten.then((stop) => stop());
  }, []);
  const button = "flex h-full w-[46px] items-center justify-center text-foreground/80 transition-colors hover:bg-foreground/10";
  return (
    <div className="flex h-full self-stretch">
      <button type="button" className={button} aria-label={t("window.minimize")} onClick={() => void getCurrentWindow().minimize()}>
        <Minus className="size-4" strokeWidth={1.5} />
      </button>
      <button
        type="button"
        className={button}
        aria-label={t(maximized ? "window.restore" : "window.maximize")}
        onClick={() => void getCurrentWindow().toggleMaximize()}
      >
        {maximized ? <Copy className="size-3.5 -scale-x-100" strokeWidth={1.5} /> : <Square className="size-3.5" strokeWidth={1.5} />}
      </button>
      <button
        type="button"
        className={cn(button, "hover:bg-[#c42b1c] hover:text-white")}
        aria-label={t("window.close")}
        onClick={() => void getCurrentWindow().close()}
      >
        <X className="size-4" strokeWidth={1.5} />
      </button>
    </div>
  );
}

export default function App() {
  const { t } = useTranslation();
  const { settings, error } = useSettings();
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => void loadSettings(), []);

  useTheme(settings?.theme);
  useJobEvents();
  useFileDrop();
  useShares();
  const tab = useEdit((s) => s.tab);
  const active = useJobs((s) => s.jobs.filter(isActive).length);

  const tabs = [
    { value: "download", icon: Download, label: t("tabs.download") },
    { value: "edit", icon: Scissors, label: t("tabs.edit") },
    { value: "jobs", icon: ListVideo, label: t("tabs.jobs") },
  ] as const;
  // Running jobs: a count after the Queue tab's name (on its icon on Android), read out after it.
  const count = (value: string, className: string) =>
    value === "jobs" && active > 0 ? (
      <>
        <span aria-hidden className={cn("flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] leading-none font-semibold tabular-nums", className)}>
          {active}
        </span>
        <span className="sr-only">, {t("tabs.active", { count: active })}</span>
      </>
    ) : null;
  const settingsButton = (className: string, children: ReactNode) => (
    <button type="button" className={className} aria-label={t("settings.title")} disabled={!settings} onClick={() => setSettingsOpen(true)}>
      {children}
    </button>
  );

  return (
    <Tabs
      value={tab}
      onValueChange={(tab) => useEdit.setState({ tab: tab as Tab })}
      // Windows draws the edge of a window without a title bar on three sides only: the top one is drawn here.
      className={cn("h-dvh gap-0 pt-[env(safe-area-inset-top)]", !isAndroid && "border-t border-t-[rgb(47_47_47)]")}
    >
      {isAndroid ? null : (
        /* The title bar: it drags the window, except over its buttons. */
        <header data-tauri-drag-region className="flex h-12 shrink-0 items-center gap-2 border-b pl-3">
          <TabsList className="h-9 gap-0.5 rounded-lg border bg-transparent p-0.5 group-data-[orientation=horizontal]/tabs:h-9">
            {tabs.map(({ value, icon: Icon, label }) => (
              <TabsTrigger
                key={value}
                value={value}
                className="h-full flex-none rounded-md border-0 px-3 text-[13px] text-muted-foreground shadow-none hover:bg-foreground/5 data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-none dark:text-muted-foreground dark:data-[state=active]:bg-primary dark:data-[state=active]:text-primary-foreground"
              >
                <Icon className="text-current" />
                {label}
                {count(value, "bg-current/15")}
              </TabsTrigger>
            ))}
          </TabsList>
          <div data-tauri-drag-region className="flex-1 self-stretch" />
          {settingsButton(
            "flex size-9 items-center justify-center rounded-lg border text-foreground/80 transition-colors hover:bg-foreground/5 hover:text-foreground disabled:opacity-50",
            <SettingsIcon className="size-4" strokeWidth={1.75} />,
          )}
          <WindowControls />
        </header>
      )}

      {error && (
        <p role="alert" className="border-b bg-destructive/10 px-4 py-2 text-sm text-destructive">
          {t(error.key, error.params)}
        </p>
      )}

      {/* Room at the bottom for the pinned action. */}
      <TabsContent value="download" className="min-h-0 overflow-y-auto px-3 pt-3 pb-20 sm:px-4">
        <DownloadTab />
      </TabsContent>
      <TabsContent value="edit" className="min-h-0 overflow-y-auto px-3 pt-3 pb-20 sm:px-4">
        <EditTab />
      </TabsContent>
      <TabsContent value="jobs" className="min-h-0 overflow-y-auto px-3 pt-3 pb-3 sm:px-4">
        <JobList />
      </TabsContent>

      {isAndroid && (
        /* Android: the tabs and the settings at the bottom, under the thumb. */
        <nav className="grid shrink-0 grid-cols-4 border-t bg-muted/40 pb-[env(safe-area-inset-bottom)]">
          <TabsList className="contents">
            {tabs.map(({ value, icon: Icon, label }) => (
              <TabsTrigger
                key={value}
                value={value}
                className="group/nav h-16 flex-col gap-1 rounded-none border-0 text-xs text-muted-foreground shadow-none outline-none focus-visible:ring-0 focus-visible:outline-none data-[state=active]:bg-transparent data-[state=active]:text-foreground data-[state=active]:shadow-none dark:data-[state=active]:bg-transparent"
              >
                <span className="relative flex h-8 w-14 items-center justify-center rounded-full transition-colors group-data-[state=active]/nav:bg-primary group-data-[state=active]/nav:text-primary-foreground">
                  <Icon className="size-5 text-current" />
                </span>
                {label}
                {/* On the icon's top right corner, read out after the name. */}
                <span className="absolute top-1.5 left-1/2 ml-3">{count(value, "bg-foreground text-background ring-2 ring-background")}</span>
              </TabsTrigger>
            ))}
          </TabsList>
          {settingsButton(
            "flex h-16 flex-col items-center justify-center gap-1 text-xs font-medium text-muted-foreground outline-none disabled:opacity-50",
            <>
              <span className="flex h-8 w-14 items-center justify-center">
                <SettingsIcon className="size-5" />
              </span>
              {t("settings.title")}
            </>,
          )}
        </nav>
      )}

      {settings && (
        <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} settings={settings} onChange={changeSettings} />
      )}
    </Tabs>
  );
}
