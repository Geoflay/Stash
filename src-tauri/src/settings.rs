use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::media::{AudioCodec, Preset};

const FILE: &str = "settings.json";

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct Settings {
    /// Windows only. `None` means the user's Downloads folder.
    pub download_dir: Option<PathBuf>,
    pub theme: Theme,
    /// Update yt-dlp when the app starts.
    pub auto_update: bool,
    /// The sound codec a re-encode writes (the picture is always H.264).
    pub audio_codec: AudioCodec,
    /// How hard a re-encode works.
    pub preset: Preset,
    /// Jobs of the queue that run at once.
    pub max_jobs: u32,
    /// Cut the parts SponsorBlock lists out of YouTube downloads.
    pub sponsor_block: bool,
    /// The SponsorBlock categories cut.
    pub sponsor_categories: Vec<String>,
    /// Named sets of edit options, kept as the interface writes them.
    pub presets: Vec<serde_json::Value>,
}

/// Every SponsorBlock category that can be cut.
pub const SPONSOR_CATEGORIES: [&str; 8] = ["sponsor", "selfpromo", "interaction", "intro", "outro", "preview", "filler", "music_offtopic"];

impl Default for Settings {
    fn default() -> Self {
        Settings {
            download_dir: None,
            theme: Theme::System,
            auto_update: true,
            audio_codec: AudioCodec::Aac,
            preset: Preset::Faster,
            max_jobs: 3,
            sponsor_block: true,
            sponsor_categories: SPONSOR_CATEGORIES.map(String::from).to_vec(),
            presets: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Theme {
    #[default]
    System,
    Light,
    Dark,
}

/// Missing or unreadable settings fall back to the defaults.
pub fn load(dir: &Path) -> Settings {
    fs::read(dir.join(FILE))
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default()
}

pub fn save(dir: &Path, settings: &Settings) -> io::Result<()> {
    fs::create_dir_all(dir)?;
    fs::write(dir.join(FILE), serde_json::to_vec_pretty(settings)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_and_defaults() {
        let dir = std::env::temp_dir().join(format!("stash-settings-{}", std::process::id()));
        assert_eq!(load(&dir), Settings::default());

        let settings = Settings {
            download_dir: Some("D:/Videos".into()),
            theme: Theme::Dark,
            auto_update: false,
            audio_codec: AudioCodec::Mp3,
            preset: Preset::Slow,
            max_jobs: 1,
            sponsor_block: false,
            sponsor_categories: vec!["sponsor".into()],
            presets: vec![serde_json::json!({"name": "Small", "format": "mp4", "height": 480})],
        };
        save(&dir, &settings).unwrap();
        assert_eq!(load(&dir), settings);

        // Unknown or missing fields keep the defaults.
        fs::write(dir.join(FILE), r#"{"theme":"light","old":1}"#).unwrap();
        assert_eq!(load(&dir), Settings { theme: Theme::Light, ..Settings::default() });
        // The picture codec, chosen before only H.264 was written, is left out.
        fs::write(dir.join(FILE), r#"{"videoCodec":"h265","audioCodec":"mp3"}"#).unwrap();
        assert_eq!(load(&dir), Settings { audio_codec: AudioCodec::Mp3, ..Settings::default() });
        fs::remove_dir_all(dir).unwrap();
    }
}
