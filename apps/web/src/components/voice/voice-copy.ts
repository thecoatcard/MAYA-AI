/**
 * Plain wording shared by the voice controls. No component here: the voice dock card is gone, so
 * these lines now live where they still matter (the approval card, the muted line, the notice
 * shown when voice connects).
 */

/** Shown on an approval card while voice is on, and once when voice connects. */
export const VOICE_APPROVAL_NOTICE =
  "Voice can read this back or deny it, but only you can approve it here.";

/** Said once when a voice session connects (an aria-live toast; there is no permanent panel). */
export const VOICE_START_NOTICE = `Voice connected. ${VOICE_APPROVAL_NOTICE}`;

export const MUTED_NOTICE =
  "Your microphone is muted. MAYA can still speak to you, and nothing you say is sent.";
