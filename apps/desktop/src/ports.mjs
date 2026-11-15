import net from "node:net";

/**
 * Ask the OS for a free loopback port. The probe socket is closed before returning, so a very
 * unlucky race is possible; the supervisor treats a failed bind as an API start failure.
 * @param {{ createServer: typeof net.createServer }} [netImpl]
 * @returns {Promise<number>}
 */
export function findFreePort(netImpl = net) {
  return new Promise((resolve, reject) => {
    const server = netImpl.createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => (port > 0 ? resolve(port) : reject(new Error("No port assigned"))));
    });
  });
}
