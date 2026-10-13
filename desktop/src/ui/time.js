// Compact stamps for one view. Other views keep their own formatters.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY = 24 * 60 * 60 * 1000;

function clock(date, zh) {
  let hour = date.getHours();
  const minute = String(date.getMinutes()).padStart(2, "0");
  const period = hour < 12 ? (zh ? "上午" : "AM") : (zh ? "下午" : "PM");
  let h12 = hour % 12;
  if (h12 === 0) h12 = 12;
  return zh ? `${period}${h12}:${minute}` : `${h12}:${minute} ${period}`;
}

function language(locale) {
  if (typeof locale === "string" && locale) return locale;
  if (typeof navigator !== "undefined" && navigator.language) return navigator.language;
  return "en";
}

export function compactTime(value, {locale, now = Date.now()} = {}) {
  if (value == null || value === "") return "";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const zh = language(locale).toLowerCase().startsWith("zh");
  const delta = now - date.getTime();
  if (delta >= 0 && delta < DAY) {
    const minutes = Math.floor(delta / 60000);
    if (minutes < 1) return zh ? "剛剛" : "just now";
    if (minutes < 60) return zh ? `${minutes} 分鐘前` : `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    return zh ? `${hours} 小時前` : `${hours}h ago`;
  }
  if (zh) return `${date.getMonth() + 1}月${date.getDate()}日 ${clock(date, true)}`;
  return `${MONTHS[date.getMonth()]} ${date.getDate()}, ${clock(date, false)}`;
}
