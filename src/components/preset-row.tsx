import { BookmarkPlus } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Row } from "@/components/options-panel";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { savePreset, useSettings, type Preset } from "@/features/settings/settings";

/** The first line of the options: the saved presets, and a button that saves the current
 * options as one. `selected` is the preset last chosen, until an option it sets changes. */
export function PresetRow({
  selected,
  current,
  onApply,
}: {
  selected: string | null;
  /** The options a preset made now would hold. */
  current: Omit<Preset, "name">;
  onApply: (preset: Preset) => void;
}) {
  const { t } = useTranslation();
  const presets = useSettings((s) => s.settings?.presets ?? []);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const value = presets.some((p) => p.name === selected) ? (selected as string) : "";
  const replaces = presets.some((p) => p.name === name.trim());

  function save() {
    const preset = { ...current, name: name.trim() };
    if (!preset.name) return;
    savePreset(preset);
    onApply(preset);
    setNaming(false);
  }

  return (
    <Row label={t("presets.label")}>
      <div className="flex items-center gap-2">
        <Select value={value} onValueChange={(name) => onApply(presets.find((p) => p.name === name)!)} disabled={!presets.length}>
          <SelectTrigger aria-label={t("presets.label")} className="min-w-0 flex-1">
            <SelectValue placeholder={presets.length ? t("presets.custom") : t("presets.none")} />
          </SelectTrigger>
          <SelectContent position="popper">
            {presets.map((p) => (
              <SelectItem key={p.name} value={p.name} className="min-h-field">
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant="outline"
          size="icon"
          className="shrink-0"
          aria-label={t("presets.save")}
          title={t("presets.save")}
          onClick={() => {
            setName(value);
            setNaming(true);
          }}
        >
          <BookmarkPlus className="text-foreground" />
        </Button>
      </div>

      <Dialog open={naming} onOpenChange={setNaming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("presets.save")}</DialogTitle>
          </DialogHeader>
          <form
            className="grid gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              save();
            }}
          >
            <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} aria-label={t("presets.name")} placeholder={t("presets.name")} />
            <p className="min-h-4 text-xs text-muted-foreground">{replaces && t("presets.replaces")}</p>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setNaming(false)}>
                {t("presets.cancel")}
              </Button>
              <Button type="submit" disabled={!name.trim()}>
                {t("presets.saveButton")}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Row>
  );
}
