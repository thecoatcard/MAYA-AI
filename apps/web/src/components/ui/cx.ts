/** Join class names, skipping falsy parts. Tiny on purpose: no dependency. */
export function cx(
  ...parts: ReadonlyArray<string | false | null | undefined>
): string {
  return parts.filter((part): part is string => Boolean(part)).join(" ");
}
