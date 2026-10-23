//! Bounded storage, unbounded draining: never stop consuming a noisy child's pipe.
use std::{collections::VecDeque, fmt::Write, time::Duration};

const MAX_MARKERS: usize = 64;
const MAX_LINE: usize = 96;
const MAX_TAIL: usize = 1024;

#[derive(Clone, Debug, Default)]
pub(super) struct Evidence {
    markers: Vec<(String, Duration)>,
    markers_dropped: usize,
    tail: VecDeque<u8>,
    dropped: usize,
    line: Vec<u8>,
    oversized: bool,
    pub eof: bool,
    pub read_error: Option<String>,
}
impl Evidence {
    pub fn feed(&mut self, bytes: &[u8], at: Duration) {
        for &byte in bytes {
            if self.oversized {
                self.tail_byte(byte);
                if byte == b'\n' {
                    self.oversized = false;
                }
            } else if byte == b'\n' {
                let line = std::mem::take(&mut self.line);
                let name = line
                    .strip_prefix(b"BATC_PHASE ")
                    .map(|v| v.strip_suffix(b"\r").unwrap_or(v));
                if let Some(name) = name.filter(|n| {
                    !n.is_empty()
                        && n.len() <= 64
                        && n.iter()
                            .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(b))
                }) {
                    if self.markers.len() == MAX_MARKERS {
                        // Preserve the early path and the latest marker (including script-end).
                        self.markers.pop();
                        self.markers_dropped += 1;
                    }
                    self.markers
                        .push((String::from_utf8(name.to_vec()).unwrap(), at));
                } else {
                    for b in line {
                        self.tail_byte(b);
                    }
                    self.tail_byte(byte);
                }
            } else {
                self.line.push(byte);
                if self.line.len() > MAX_LINE {
                    for b in std::mem::take(&mut self.line) {
                        self.tail_byte(b);
                    }
                    self.oversized = true;
                }
            }
        }
    }
    fn tail_byte(&mut self, byte: u8) {
        if self.tail.len() == MAX_TAIL {
            self.tail.pop_front();
            self.dropped += 1;
        }
        self.tail.push_back(byte);
    }
    pub fn describe(&self, exited: bool) -> String {
        let mut out = String::from("phases=[");
        for (i, (name, at)) in self.markers.iter().enumerate() {
            if i != 0 {
                out.push_str(", ");
            }
            write!(out, "{name}@{at:?}").unwrap();
        }
        write!(
            out,
            "]; phases_dropped={}; stderr_EOF={}; stderr_read_error={:?}",
            self.markers_dropped, self.eof, self.read_error
        )
        .unwrap();
        if !exited
            && self
                .markers
                .last()
                .is_some_and(|(name, _)| name == "script-end")
        {
            out.push_str("; stall=shutdown (script-end reached, process exit not observed)");
        }
        let mut tail = self.clone();
        for byte in std::mem::take(&mut tail.line) {
            tail.tail_byte(byte);
        }
        let bytes: Vec<_> = tail.tail.into_iter().collect();
        // Escaping avoids terminal controls; cap the rendered tail too (UTF-8
        // replacement and escapes can expand the raw byte count).
        let escaped: String = String::from_utf8_lossy(&bytes)
            .chars()
            .flat_map(char::escape_default)
            .collect();
        let suffix = &escaped[escaped.len().saturating_sub(2048)..]; // escape_default is ASCII
        write!(
            out,
            "; stderr_dropped={}; stderr_tail={suffix}",
            tail.dropped
        )
        .unwrap();
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn partial_lines_marker_overflow_and_non_utf8_remain_bounded() {
        let mut evidence = Evidence::default();
        evidence.feed(b"BATC_PHA", Duration::ZERO);
        evidence.feed(b"SE first\r\n", Duration::from_millis(1));
        for _ in 0..1000 {
            evidence.feed(b"BATC_PHASE middle\n", Duration::from_millis(2));
        }
        evidence.feed(&[255; 8192], Duration::from_millis(3));
        evidence.feed(b"\nBATC_PHASE script-end\n", Duration::from_millis(4));
        let text = evidence.describe(false);
        assert!(text.contains("first@1ms"));
        assert!(text.contains("script-end@4ms"));
        assert!(text.contains("stall=shutdown"));
        assert_eq!(evidence.markers.len(), MAX_MARKERS);
        assert_eq!(evidence.tail.len(), MAX_TAIL);
        assert!(text.len() < 8192);
    }
}
