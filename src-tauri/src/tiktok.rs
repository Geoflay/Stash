//! TikTok photo posts: yt-dlp only gives their sound, so the photos are read from the
//! post's page (the `__UNIVERSAL_DATA_FOR_REHYDRATION__` JSON) and downloaded here.

use std::fs::File;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tauri::AppHandle;
use ureq::ResponseExt;

use crate::Failure;

const USER_AGENT: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36";
/// The CDN refuses photo and sound requests without it.
const REFERER: &str = "https://www.tiktok.com/";

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Photo {
    pub url: String,
    pub width: u32,
    pub height: u32,
}

fn host(url: &str) -> &str {
    let rest = url.split_once("://").map_or(url, |(_, rest)| rest);
    rest.split(['/', '?', '#']).next().unwrap_or("")
}

fn is_tiktok_host(host: &str) -> bool {
    let host = host.to_ascii_lowercase();
    host == "tiktok.com" || host.ends_with(".tiktok.com")
}

pub fn is_tiktok(url: &str) -> bool {
    is_tiktok_host(host(url))
}

/// The lowercased path of a link, without its query.
fn url_path(url: &str) -> String {
    url.split('?').next().unwrap_or("").to_ascii_lowercase()
}

fn is_short_link(url: &str) -> bool {
    matches!(host(url).to_ascii_lowercase().as_str(), "vm.tiktok.com" | "vt.tiktok.com")
}

/// yt-dlp only reads `/video/<id>` links: a photo post's `/photo/<id>` becomes `/video/<id>`.
/// Returns the link and whether it named a photo post.
pub fn normalize(url: &str) -> (String, bool) {
    if !is_tiktok(url) || !url.contains("/photo/") {
        return (url.to_string(), false);
    }
    (url.replacen("/photo/", "/video/", 1), true)
}

/// One agent for every request, so a post's photos reuse the connection.
fn agent() -> &'static ureq::Agent {
    static AGENT: OnceLock<ureq::Agent> = OnceLock::new();
    AGENT.get_or_init(|| ureq::Agent::config_builder().timeout_global(Some(Duration::from_secs(30))).build().into())
}

fn get(url: &str, cookies: Option<&str>) -> Result<ureq::http::Response<ureq::Body>, ureq::Error> {
    let mut request = agent().get(url).header("User-Agent", USER_AGENT).header("Referer", REFERER);
    if let Some(cookies) = cookies {
        request = request.header("Cookie", cookies);
    }
    request.call()
}

fn network(err: impl std::fmt::Display) -> Failure {
    Failure::new("errors.network", json!({ "detail": err.to_string() }))
}

/// Follows a vm.tiktok.com / vt.tiktok.com short link to the post's address.
pub fn resolve(url: &str) -> Result<String, Failure> {
    if !is_short_link(url) {
        return Ok(url.to_string());
    }
    let response = get(url, None).map_err(network)?;
    Ok(response.get_uri().to_string())
}

/// The `Cookie` header for TikTok from the imported cookies.txt, if any.
pub fn saved_cookies(app: &AppHandle) -> Option<String> {
    cookie_header(&std::fs::read_to_string(crate::cookies_path(app).ok()?).ok()?)
}

/// The `Cookie` header for TikTok from a cookies.txt file (Netscape format).
pub fn cookie_header(cookies_txt: &str) -> Option<String> {
    let pairs: Vec<String> = cookies_txt
        .lines()
        // curl marks HttpOnly cookies with this prefix; they are cookies all the same.
        .map(|line| line.strip_prefix("#HttpOnly_").unwrap_or(line))
        .filter(|line| !line.starts_with('#'))
        .filter_map(|line| {
            let fields: Vec<&str> = line.split('\t').collect();
            let [domain, _, _, _, _, name, value] = fields[..] else { return None };
            is_tiktok_host(domain.trim_start_matches('.')).then(|| format!("{name}={value}"))
        })
        .collect();
    (!pairs.is_empty()).then(|| pairs.join("; "))
}

/// The photos in a post page; `None` when the page has no photo data.
pub fn parse_photos(html: &str) -> Option<Vec<Photo>> {
    let start = html.find("id=\"__UNIVERSAL_DATA_FOR_REHYDRATION__\"")?;
    let json_start = start + html[start..].find('>')? + 1;
    let json_end = json_start + html[json_start..].find("</script>")?;
    let data: Value = serde_json::from_str(&html[json_start..json_end]).ok()?;
    let images = data["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]["imagePost"]["images"].as_array()?;
    let photos: Vec<Photo> = images
        .iter()
        .filter_map(|image| {
            let urls: Vec<&str> = image["imageURL"]["urlList"].as_array()?.iter().filter_map(Value::as_str).collect();
            // JPEG or PNG when offered: every decoder reads them.
            let url = urls.iter().find(|u| [".jpeg", ".jpg", ".png"].iter().any(|ext| url_path(u).ends_with(ext))).or(urls.first())?;
            let size = |key: &str| image[key].as_u64().map(|n| n as u32);
            Some(Photo { url: url.to_string(), width: size("imageWidth")?, height: size("imageHeight")? })
        })
        .collect();
    (!photos.is_empty()).then_some(photos)
}

/// Reads the photos of a post from its page.
pub fn photos(url: &str, cookies: Option<&str>) -> Result<Vec<Photo>, Failure> {
    let html = get(url, cookies).map_err(network)?.body_mut().read_to_string().map_err(network)?;
    parse_photos(&html).ok_or_else(|| Failure::plain("errors.noPhotos"))
}

/// The file extension for a downloaded photo.
pub fn extension(content_type: Option<&str>, url: &str) -> &'static str {
    let path = url_path(url);
    match content_type.unwrap_or("") {
        "image/png" => "png",
        "image/webp" => "webp",
        "image/jpeg" => "jpg",
        _ if path.ends_with(".png") => "png",
        _ if path.ends_with(".webp") => "webp",
        _ => "jpg",
    }
}

/// Downloads a photo to `stem` plus its extension; returns the file written.
pub fn download(url: &str, stem: &Path, cookies: Option<&str>) -> Result<PathBuf, Failure> {
    let mut response = get(url, cookies).map_err(network)?;
    let content_type = response.headers().get("content-type").and_then(|v| v.to_str().ok()).map(|v| v.split(';').next().unwrap_or("").trim().to_string());
    let path = stem.with_extension(extension(content_type.as_deref(), url));
    let mut file = File::create(&path).map_err(Failure::io)?;
    io::copy(&mut response.body_mut().as_reader(), &mut file).map_err(network)?;
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn links() {
        let photo = "https://www.tiktok.com/@love_horsys/photo/7678775357801123104?_r=1&_t=ZG";
        assert_eq!(normalize(photo), ("https://www.tiktok.com/@love_horsys/video/7678775357801123104?_r=1&_t=ZG".into(), true));
        let video = "https://www.tiktok.com/@a/video/1";
        assert_eq!(normalize(video), (video.into(), false));
        // Only TikTok links change.
        assert!(!normalize("https://example.com/photo/1").1);
        assert!(is_tiktok("https://m.tiktok.com/v/1") && is_tiktok("tiktok.com/@a"));
        assert!(!is_tiktok("https://nottiktok.com/@a") && !is_tiktok("https://example.com/?u=tiktok.com"));
        assert!(is_short_link("https://vm.tiktok.com/ZGabc/") && is_short_link("https://VT.tiktok.com/x"));
        assert!(!is_short_link("https://www.tiktok.com/@a/video/1"));
    }

    #[test]
    fn photos_from_the_page() {
        let html = std::fs::read_to_string("tests/fixtures/tiktok-photo.html").unwrap();
        let photos = parse_photos(&html).unwrap();
        assert_eq!(photos.len(), 2);
        assert_eq!((photos[0].width, photos[0].height), (1168, 1548));
        assert!(photos[0].url.contains("photomode-image.jpeg?"));
        // A list that starts with WebP still gives the JPEG.
        assert!(photos[1].url.contains(".jpeg?"));
        assert_eq!(parse_photos("<html>no data</html>"), None);
        let empty = r#"<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{"__DEFAULT_SCOPE__":{}}</script>"#;
        assert_eq!(parse_photos(empty), None);
    }

    #[test]
    fn cookies_for_tiktok_only() {
        let txt = "# Netscape HTTP Cookie File\n.tiktok.com\tTRUE\t/\tTRUE\t0\tsid\tabc\n#HttpOnly_www.tiktok.com\tFALSE\t/\tTRUE\t0\ttt\t1\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tno\n";
        assert_eq!(cookie_header(txt).as_deref(), Some("sid=abc; tt=1"));
        assert_eq!(cookie_header(".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tno\n"), None);
    }

    #[test]
    fn photo_extensions() {
        assert_eq!(extension(Some("image/png"), "https://x/a.jpeg"), "png");
        assert_eq!(extension(None, "https://x/a.webp?x=1"), "webp");
        assert_eq!(extension(Some("application/octet-stream"), "https://x/a"), "jpg");
    }
}
