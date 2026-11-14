// MAYA's spoken hello on the start screen. It runs before any web page exists, so it uses the PC's
// own offline text-to-speech and picks a FEMALE voice by name (the OS gives no gender field). The
// same ranking lives in apps/web/src/lib/tts-voice.ts for the in-app "Speak" button; keep the two
// lists in step. The picker is self-contained so its source can be injected into the start page.

/**
 * @param {{ name: string, lang: string }[]} voices
 * @returns {{ name: string, lang: string } | null}
 */
export function pickFemaleVoice(voices) {
  const female = ["aria", "jenny", "ava", "emma", "michelle", "sonia", "libby", "natasha", "clara", "swara",
    "neerja", "heera", "zira", "hazel", "susan", "samantha", "karen", "victoria", "tessa", "moira", "fiona", "female"];
  const male = ["david", "mark", "george", "guy", "james", "ryan", "christopher", "eric", "roger", "steffan",
    "daniel", "alex", "fred", "ravi", "prabhat", "hemant", "madhur", "rishi", "male"];
  const preferred = ["aria", "jenny", "ava", "emma", "michelle", "samantha"];
  const has = (name, words) => words.some((w) => new RegExp("\\b" + w + "\\b", "i").test(name));
  let best = null;
  let bestScore = 0;
  for (const voice of voices) {
    const name = voice.name;
    if (has(name, male) && !/female/i.test(name)) continue;
    if (!has(name, female) && !/google (us|uk) english/i.test(name)) continue;
    let score = 10;
    if (/online|natural|neural/i.test(name)) score += 50;
    if (has(name, preferred)) score += 30;
    if (/female/i.test(name)) score += 10;
    if (/^en[-_]/i.test(voice.lang)) score += 20;
    if (/^en[-_](us|in|gb)/i.test(voice.lang)) score += 5;
    if (score > bestScore) {
      best = voice;
      bestScore = score;
    }
  }
  return best;
}

/**
 * The script run inside the start page. It waits (up to 2 s) for the voice list, which many
 * engines fill in late, then speaks with the chosen female voice. If none can be told apart it
 * stays quiet rather than speak in a man's voice.
 * @param {string} text
 */
export function greetingScript(text) {
  return `(() => new Promise((resolve) => {
    const pick = ${pickFemaleVoice.toString()};
    const speak = () => {
      try {
        const voice = pick(speechSynthesis.getVoices());
        if (!voice) return resolve(false);
        const u = new SpeechSynthesisUtterance(${JSON.stringify(text)});
        u.voice = speechSynthesis.getVoices().find((v) => v.name === voice.name) || null;
        u.lang = voice.lang; u.rate = 1; u.pitch = 1.08; u.volume = 0.9;
        u.onend = () => resolve(true);
        u.onerror = () => resolve(false);
        speechSynthesis.cancel(); speechSynthesis.speak(u);
      } catch { resolve(false); }
    };
    if (speechSynthesis.getVoices().length > 0) return speak();
    let done = false;
    const go = () => { if (done) return; done = true; speak(); };
    speechSynthesis.addEventListener("voiceschanged", go, { once: true });
    setTimeout(go, 2000);
  }))()`;
}
