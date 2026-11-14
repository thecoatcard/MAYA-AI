/**
 * Plain-language text for the reason a live voice session cannot be used (the `reason` of a
 * LIVE_UNAVAILABLE frame). The code stays in brackets so a support request can quote it.
 */
export function describeVoiceUnavailable(reason: string | null): string {
  const tail = " Text chat still works.";
  switch (reason) {
    case "connect_failed":
      return (
        "MAYA could not reach the voice service (connect_failed). Check your internet " +
        "connection and try again." +
        tail
      );
    case "quota":
      return (
        "The voice service is over its limit right now (quota). Try again in a minute." +
        tail
      );
    case "no_key":
      return (
        "Voice is not set up: no Gemini key is configured on the server (no_key)." +
        tail
      );
    case "disabled":
      return "Voice is turned off on this server (disabled)." + tail;
    case null:
    case "":
      return "Voice is unavailable." + tail;
    default:
      return `Voice is unavailable (${reason}).` + tail;
  }
}
