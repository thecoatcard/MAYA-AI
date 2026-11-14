import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// `findBy*` and `waitFor` give up after 1 s by default. On a busy machine (a full suite beside a
// browser run, or a CI worker) a heavy view can take longer than that to settle, which made
// otherwise correct tests fail at random. A longer ceiling does not slow a passing test: the
// query still resolves the moment the element appears.
configure({ asyncUtilTimeout: 4000 });

afterEach(cleanup);
