import assert from "node:assert/strict";
import test from "node:test";

import { greetingScript, pickFemaleVoice } from "../src/greeting.mjs";

const v = (name, lang = "en-US") => ({ name, lang });

test("prefers a natural female voice", () => {
  const picked = pickFemaleVoice([
    v("Microsoft David - English (United States)"),
    v("Microsoft Zira - English (United States)"),
    v("Microsoft Aria Online (Natural) - English (United States)"),
  ]);
  assert.match(picked.name, /Aria/);
});

test("never picks a male voice", () => {
  assert.equal(
    pickFemaleVoice([v("Microsoft David - English (United States)"), v("Microsoft Mark - English (United States)"), v("Alex")]),
    null,
  );
});

test("knows the Chrome and macOS female voices and handles an empty list", () => {
  assert.equal(pickFemaleVoice([v("Alex"), v("Samantha")]).name, "Samantha");
  assert.equal(pickFemaleVoice([]), null);
});

test("the injected script embeds the picker and the text safely", () => {
  const script = greetingScript('Hi, I\'m "MAYA". </script>');
  assert.match(script, /voiceschanged/);
  assert.match(script, /pitch = 1\.08/);
  assert.ok(script.includes(JSON.stringify('Hi, I\'m "MAYA". </script>')));
  // The generated source must be valid JavaScript.
  assert.doesNotThrow(() => new Function(`return ${script.replace(/^\(\(\) =>/, "(() =>")}`));
});
