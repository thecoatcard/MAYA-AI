/**
 * Picks MAYA's browser/OS text-to-speech voice: always a female voice, the most natural one
 * available. The browser offers no gender field, so the choice is made from the voice names that
 * Windows, macOS, Chrome and Edge ship. A voice whose name is on the male list is never chosen, and
 * when no female voice can be told apart the function returns null (the caller then keeps the
 * system default rather than guessing a man's voice).
 *
 * The same ranking is duplicated in apps/desktop/src/greeting.mjs for the start screen (it runs
 * before any web page exists); keep the two lists in step.
 */

export interface VoiceLike {
  readonly name: string;
  readonly lang: string;
}

const FEMALE_NAMES = [
  "aria",
  "jenny",
  "ava",
  "emma",
  "michelle",
  "sonia",
  "libby",
  "natasha",
  "clara",
  "swara",
  "neerja",
  "heera",
  "zira",
  "hazel",
  "susan",
  "samantha",
  "karen",
  "victoria",
  "tessa",
  "moira",
  "fiona",
  "female",
];

const MALE_NAMES = [
  "david",
  "mark",
  "george",
  "guy",
  "james",
  "ryan",
  "christopher",
  "eric",
  "roger",
  "steffan",
  "daniel",
  "alex",
  "fred",
  "ravi",
  "prabhat",
  "hemant",
  "madhur",
  "rishi",
  "male",
];

/** The sweetest, most natural first-choice names get a bonus. */
const PREFERRED = ["aria", "jenny", "ava", "emma", "michelle", "samantha"];

function has(name: string, words: readonly string[]): boolean {
  return words.some((word) => new RegExp(`\\b${word}\\b`, "i").test(name));
}

export function scoreVoice(voice: VoiceLike): number {
  const name = voice.name;
  if (has(name, MALE_NAMES) && !/female/i.test(name)) return -1;
  if (!has(name, FEMALE_NAMES) && !/google (us|uk) english/i.test(name)) {
    return -1;
  }
  let score = 10;
  if (/online|natural|neural/i.test(name)) score += 50;
  if (has(name, PREFERRED)) score += 30;
  if (/female/i.test(name)) score += 10;
  if (/^en[-_]/i.test(voice.lang)) score += 20;
  if (/^en[-_](us|in|gb)/i.test(voice.lang)) score += 5;
  return score;
}

export function pickFemaleVoice<T extends VoiceLike>(
  voices: readonly T[],
): T | null {
  let best: T | null = null;
  let bestScore = 0;
  for (const voice of voices) {
    const score = scoreVoice(voice);
    if (score > bestScore) {
      best = voice;
      bestScore = score;
    }
  }
  return best;
}

/** A slightly lighter pitch makes the voice friendlier; normal speed stays easy to follow. */
export const MAYA_TTS_PITCH = 1.08;
export const MAYA_TTS_RATE = 1.0;
