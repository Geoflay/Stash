import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { open } from "@tauri-apps/plugin-dialog";
import { Monitor, Moon, RefreshCw, Sun, Trash2 } from "lucide-react";
import { EditableTitle } from "@/components/editable-title";
import { ErrorNote, Hint } from "@/components/notice";
import { Choice, formatName } from "@/components/options-panel";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { Segmented } from "@/components/segmented";
import { AUDIO_CODECS } from "@/features/download/store";
import { joinDetails } from "@/lib/format";
import { isAndroid } from "@/lib/platform";
import { cn } from "@/lib/utils";
import {
  AV1_PRESETS,
  GPU_PRESETS,
  H264_PRESETS,
  PRIORITIES,
  SPONSOR_CATEGORIES,
  VIDEO_CODECS,
  cookiesImported,
  deletePreset,
  renamePreset,
  importCookies,
  removeCookies,
  updateYtdlp,
  ytdlpVersion,
  type Failure,
  type Preset,
  type Settings,
  type Theme,
} from "./settings";

const JOB_LIMITS = ["1", "2", "3", "4", "5"] as const;

const themeIcons: Record<Theme, ReactNode> = {
  system: <Monitor className="text-foreground" />,
  light: <Sun className="text-foreground" />,
  dark: <Moon className="text-foreground" />,
};

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section className="grid gap-2">
      <h3 className="text-sm font-medium">{label}</h3>
      {children}
    </section>
  );
}

/** Numbered presets: the slowest and the fastest are named, the others are numbers. */
function numbered(presets: number[], slowest: number, fastest: number) {
  const end = (preset: number) => (preset === slowest ? "slowest" : preset === fastest ? "fastest" : undefined);
  return (t: TFunction) => presets.map((p) => ({ value: String(p), label: t("settings.numberedPresets", { context: end(p), preset: p }) }));
}
const av1Items = numbered(AV1_PRESETS, 0, 13);
const gpuItems = numbered(GPU_PRESETS, 7, 1);

/** What a preset sets, in short. */
function usePresetSummary() {
  const { t } = useTranslation();
  return ({ format, height, mute, speed, fps, maxMb }: Preset) =>
    joinDetails([
      formatName(t, format),
      height !== null && t("options.height", { height }),
      mute && t("options.mute"),
      speed !== 1 && t("presets.speed", { speed }),
      fps !== null && t("presets.fps", { fps }),
      maxMb !== null && t("presets.maxMb", { mb: maxMb }),
    ]);
}

/** A setting turned on or off. */
function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="flex min-h-field items-center justify-between gap-2 text-sm">
      {label}
      <Switch checked={checked} onCheckedChange={onChange} />
    </label>
  );
}

export function SettingsDialog({
  open: isOpen,
  onOpenChange,
  settings,
  onChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  settings: Settings;
  onChange: (patch: Partial<Settings>) => void;
}) {
  const { t } = useTranslation();
  // undefined while checking, null when unavailable.
  const [version, setVersion] = useState<string | null | undefined>(undefined);
  const [updating, setUpdating] = useState(false);
  const [updated, setUpdated] = useState<boolean | null>(null);
  const [cookies, setCookies] = useState(false);
  const [error, setError] = useState<Failure | null>(null);
  const summary = usePresetSummary();

  useEffect(() => {
    if (!isOpen) return;
    ytdlpVersion()
      .then(setVersion)
      .catch(() => setVersion(null));
    cookiesImported().then(setCookies, setError);
  }, [isOpen]);

  async function update() {
    setUpdating(true);
    setError(null);
    try {
      const [installed, changed] = await updateYtdlp();
      setVersion(installed);
      setUpdated(changed);
    } catch (e) {
      setError(e as Failure);
    } finally {
      setUpdating(false);
    }
  }

  async function chooseCookies() {
    const path = await open(isAndroid ? {} : { filters: [{ name: "cookies.txt", extensions: ["txt"] }] });
    if (!path) return;
    setError(null);
    try {
      await importCookies(path);
      setCookies(true);
    } catch (e) {
      setError(e as Failure);
    }
  }

  async function forgetCookies() {
    await removeCookies().catch(setError);
    setCookies(false);
  }

  let versionText = version === undefined ? t("settings.ytdlpLoading") : (version ?? t("settings.ytdlpUnavailable"));
  if (version && updated !== null) versionText = t(updated ? "settings.ytdlpUpdated" : "settings.ytdlpUpToDate", { version });

  async function chooseFolder() {
    const dir = await open({ directory: true, defaultPath: settings.downloadDir ?? undefined });
    if (dir) onChange({ downloadDir: dir });
  }

  return (
    <Dialog open={isOpen} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("settings.title")}</DialogTitle>
        </DialogHeader>

        <Row label={t("settings.folder")}>
          {isAndroid ? (
            <p className="text-sm text-muted-foreground">{t("settings.folderAndroid")}</p>
          ) : (
            <div className="flex items-center gap-2">
              <p className="min-w-0 flex-1 truncate text-sm text-muted-foreground" title={settings.downloadDir ?? ""}>
                {settings.downloadDir}
              </p>
              <Button variant="outline" className="shrink-0" onClick={() => void chooseFolder()}>
                {t("settings.change")}
              </Button>
            </div>
          )}
        </Row>

        <Row label={t("settings.theme")}>
          <Segmented
            label={t("settings.theme")}
            value={settings.theme}
            onChange={(theme) => onChange({ theme })}
            options={(["system", "light", "dark"] as const).map((value) => ({
              value,
              label: (
                <>
                  {themeIcons[value]}
                  {t(`settings.themes.${value}`)}
                </>
              ),
            }))}
          />
        </Row>

        <Row label={t("settings.codecs")}>
          {/* MKV holds every sound codec the app writes; a file that can't hold one gets its usual one. */}
          <div className="grid gap-2">
            <Choice
              label={t("options.audioCodec")}
              value={settings.audioCodec}
              onChange={(audioCodec) => onChange({ audioCodec })}
              items={AUDIO_CODECS.mkv.map((c) => ({ value: c, label: t(`options.codecs.${c}`) }))}
            />
            <Choice
              label={t("settings.videoCodec")}
              value={settings.videoCodec}
              onChange={(videoCodec) => onChange({ videoCodec })}
              items={VIDEO_CODECS.map((c) => ({ value: c, label: t(`settings.videoCodecs.${c}`) }))}
            />
            {/* Each encoder has its own presets, each kept. */}
            {settings.videoCodec === "av1" ? (
              <Choice
                label={t("settings.preset")}
                value={String(settings.av1Preset)}
                onChange={(preset) => onChange({ av1Preset: Number(preset) })}
                items={av1Items(t)}
              />
            ) : (
              <Choice
                label={t("settings.preset")}
                value={settings.h264Preset}
                onChange={(h264Preset) => onChange({ h264Preset })}
                items={H264_PRESETS.map((p) => ({ value: p, label: t(`settings.h264Presets.${p}`) }))}
              />
            )}
          </div>
        </Row>

        <Row label={t("settings.encoding")}>
          <Choice
            label={t("settings.priority")}
            value={settings.encodingPriority}
            onChange={(encodingPriority) => onChange({ encodingPriority })}
            items={PRIORITIES.map((p) => ({ value: p, label: t(`settings.priorities.${p}`) }))}
          />
          {/* Phones' encoders lose too much detail at the sizes asked. */}
          {!isAndroid && (
            <>
              <Toggle label={t("settings.gpu")} checked={settings.gpu} onChange={(gpu) => onChange({ gpu })} />
              <Hint>{t("settings.gpuHint")}</Hint>
              {settings.gpu && (
                <Choice
                  label={t("settings.gpuPreset")}
                  value={String(settings.gpuPreset)}
                  onChange={(preset) => onChange({ gpuPreset: Number(preset) })}
                  items={gpuItems(t)}
                />
              )}
            </>
          )}
        </Row>

        <Row label={t("settings.presetList")}>
          {settings.presets.length ? (
            <ul className="grid divide-y">
              {settings.presets.map((preset) => (
                <li key={preset.name} className="flex items-center gap-2 py-1.5 first:pt-0 last:pb-0">
                  <div className="min-w-0 flex-1">
                    <EditableTitle
                      value={preset.name}
                      onRename={(name) => renamePreset(preset.name, name)}
                      label={t("presets.name")}
                      className="text-sm"
                    />
                    <p className="truncate text-xs text-muted-foreground">{summary(preset)}</p>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="shrink-0"
                    aria-label={t("settings.deletePreset", { name: preset.name })}
                    onClick={() => deletePreset(preset.name)}
                  >
                    <Trash2 className="text-foreground" />
                  </Button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">{t("settings.presetsEmpty")}</p>
          )}
        </Row>

        <Row label={t("settings.queue")}>
          <Segmented
            label={t("settings.maxJobs")}
            value={String(settings.maxJobs) as (typeof JOB_LIMITS)[number]}
            onChange={(value) => onChange({ maxJobs: Number(value) })}
            options={JOB_LIMITS.map((value) => ({ value, label: value }))}
          />
          <Hint>{t("settings.maxJobsHint")}</Hint>
        </Row>

        <section className="grid gap-2">
          <label className="flex items-center justify-between gap-2 text-sm font-medium">
            {t("settings.sponsorBlock")}
            <Switch checked={settings.sponsorBlock} onCheckedChange={(sponsorBlock) => onChange({ sponsorBlock })} />
          </label>
          {settings.sponsorBlock && (
            <div role="group" aria-label={t("settings.sponsorCategories")} className="flex flex-wrap gap-1.5">
              {SPONSOR_CATEGORIES.map((category) => {
                const on = settings.sponsorCategories.includes(category);
                const others = settings.sponsorCategories.filter((c) => c !== category);
                return (
                  <Button
                    key={category}
                    size="sm"
                    variant={on ? "default" : "outline"}
                    className="rounded-full"
                    aria-pressed={on}
                    onClick={() => onChange({ sponsorCategories: on ? others : [...others, category] })}
                  >
                    {t(`settings.sponsorCategoryNames.${category}`)}
                  </Button>
                );
              })}
            </div>
          )}
        </section>

        <Row label={t("settings.ytdlp")}>
          <div className="flex items-center gap-2">
            <p className="min-w-0 flex-1 truncate text-sm text-muted-foreground">{versionText}</p>
            <Button variant="outline" className="shrink-0" disabled={updating} onClick={() => void update()}>
              <RefreshCw className={cn("text-foreground", updating && "animate-spin")} />
              {t("settings.ytdlpCheck")}
            </Button>
          </div>
          <Toggle label={t("settings.autoUpdate")} checked={settings.autoUpdate} onChange={(autoUpdate) => onChange({ autoUpdate })} />
        </Row>

        <Row label={t("settings.cookies")}>
          <div className="flex items-center gap-2">
            <p className="min-w-0 flex-1 truncate text-sm text-muted-foreground">
              {t(cookies ? "settings.cookiesImported" : "settings.cookiesNone")}
            </p>
            {cookies && (
              <Button variant="ghost" className="shrink-0" onClick={() => void forgetCookies()}>
                {t("settings.remove")}
              </Button>
            )}
            <Button variant="outline" className="shrink-0" onClick={() => void chooseCookies()}>
              {t("settings.import")}
            </Button>
          </div>
          <Hint>{t("settings.cookiesHint")}</Hint>
        </Row>

        {error && <ErrorNote error={error} />}
      </DialogContent>
    </Dialog>
  );
}
