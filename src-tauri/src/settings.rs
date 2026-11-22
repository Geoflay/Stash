use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Deserializer, Serialize};

use crate::media::{AV1_PRESETS, AudioCodec, Encoder, H264Preset, VideoCodec};
use crate::priority::Priority;

const FILE: &str = "settings.json";

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct Settings {
    /// Windows only. `None` means the user's Downloads folder.
    pub download_dir: Option<PathBuf>,
    pub theme: Theme,
    /// Update yt-dlp when the app starts.
    pub auto_update: bool,
    /// The sound codec a re-encode writes.
    pub audio_codec: AudioCodec,
    /// The picture codec a re-encode writes: H.264 or AV1.
    #[serde(deserialize_with = "encodable")]
    pub video_codec: VideoCodec,
    /// How hard x264 works (once saved as "preset", when it was the only encoder).
    #[serde(alias = "preset")]
    pub h264_preset: H264Preset,
    /// How hard SVT-AV1 works, from 0 (slowest) to 13.
    pub av1_preset: u8,
    /// The priority encodes run at.
    pub encoding_priority: Priority,
    /// Encode with the graphics card when it can (Windows only).
    pub gpu: bool,
    /// How hard the graphics card works, from 1 (fastest) to 7.
    pub gpu_preset: u8,
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
            video_codec: VideoCodec::H264,
            h264_preset: H264Preset::Faster,
            av1_preset: 8,
            encoding_priority: Priority::BelowNormal,
            gpu: false,
            // As good as 7 on game recordings, in half the time.
            gpu_preset: 4,
            max_jobs: 3,
            sponsor_block: true,
            sponsor_categories: SPONSOR_CATEGORIES.map(String::from).to_vec(),
            presets: Vec::new(),
        }
    }
}

impl Settings {
    /// The picture encoder of a re-encode, at its preset.
    pub fn encoder(&self) -> Encoder {
        match self.video_codec {
            VideoCodec::Av1 => Encoder::Av1(self.av1_preset.min(*AV1_PRESETS.end())),
            _ => Encoder::H264(self.h264_preset),
        }
    }
}

/// H.264 unless AV1: the app writes no other picture codec (an older setting said "h265").
fn encodable<'de, D: Deserializer<'de>>(deserializer: D) -> Result<VideoCodec, D::Error> {
    let value = serde_json::Value::deserialize(deserializer)?;
    Ok(if value == "av1" { VideoCodec::Av1 } else { VideoCodec::H264 })
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
            video_codec: VideoCodec::Av1,
            h264_preset: H264Preset::Slow,
            av1_preset: 4,
            encoding_priority: Priority::Idle,
            gpu: true,
            gpu_preset: 7,
            max_jobs: 1,
            sponsor_block: false,
            sponsor_categories: vec!["sponsor".into()],
            presets: vec![serde_json::json!({"name": "Small", "format": "mp4", "height": 480})],
        };
        save(&dir, &settings).unwrap();
        assert_eq!(load(&dir), settings);
        assert_eq!(settings.encoder(), Encoder::Av1(4));
        assert_eq!(Settings { av1_preset: 20, ..settings.clone() }.encoder(), Encoder::Av1(13));
        assert_eq!(Settings { video_codec: VideoCodec::H264, ..settings }.encoder(), Encoder::H264(H264Preset::Slow));

        // Unknown or missing fields keep the defaults.
        fs::write(dir.join(FILE), r#"{"theme":"light","old":1}"#).unwrap();
        assert_eq!(load(&dir), Settings { theme: Theme::Light, ..Settings::default() });
        // H.265, which the app no longer writes, is H.264; x264's preset, once "preset", is read.
        fs::write(dir.join(FILE), r#"{"videoCodec":"h265","audioCodec":"mp3","preset":"medium"}"#).unwrap();
        assert_eq!(load(&dir), Settings { audio_codec: AudioCodec::Mp3, h264_preset: H264Preset::Medium, ..Settings::default() });
        fs::remove_dir_all(dir).unwrap();
    }
}
