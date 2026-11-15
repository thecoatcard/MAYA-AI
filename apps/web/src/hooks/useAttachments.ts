import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  MAX_UPLOAD_FILES,
  checkUploadFile,
  deleteUpload,
  describeFilesError,
  uploadFiles,
  type UploadedFile,
} from "../lib/files-client";

/** One file in the composer. Files upload as soon as they are chosen, not when the message is sent. */
export interface AttachmentItem {
  readonly key: string;
  /** Upload id once stored. */
  readonly id: string | null;
  readonly name: string;
  readonly size: number;
  readonly kind: string;
  readonly status: "uploading" | "ready" | "error";
  /** 0 to 100 while uploading. */
  readonly progress: number;
  readonly error?: string;
  /** Uploaded by this composer (removing it also deletes the stored copy). */
  readonly owned: boolean;
}

/** What a sent message remembers about an attachment. */
export interface AttachmentRef {
  readonly id: string;
  readonly name: string;
  readonly kind: string;
}

export interface UseAttachmentsResult {
  readonly items: readonly AttachmentItem[];
  readonly uploading: boolean;
  readonly count: number;
  /** A plain notice about the last add (for example "Only 10 files fit in one message"). */
  readonly notice: string | null;
  readonly dismissNotice: () => void;
  /** Check, then upload each file on its own so one failure never loses the others. */
  readonly addFiles: (files: readonly File[]) => void;
  /** Attach a file that is already uploaded (Settings > Files > Use in chat). */
  readonly addExisting: (file: UploadedFile) => void;
  readonly remove: (key: string) => void;
  /** The ready files, and clear every chip (called when a message is sent). */
  readonly take: () => AttachmentRef[];
  readonly clear: () => void;
}

/** Guess a kind from the name until the server answers, so the chip shows the right icon. */
function provisionalKind(name: string): string {
  const ext = name.slice(name.lastIndexOf(".") + 1).toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext))
    return "image";
  if (ext === "md") return "markdown";
  if (["csv", "json", "docx", "xlsx", "pptx", "pdf"].includes(ext)) return ext;
  if (ext === "txt" || ext === "log") return "text";
  return "code";
}

export function useAttachments(): UseAttachmentsResult {
  const [items, setItems] = useState<readonly AttachmentItem[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const itemsRef = useRef<readonly AttachmentItem[]>([]);
  const controllers = useRef(new Map<string, AbortController>());
  const counter = useRef(0);
  const mounted = useRef(true);

  const commit = useCallback(
    (
      update: (prev: readonly AttachmentItem[]) => readonly AttachmentItem[],
    ) => {
      const next = update(itemsRef.current);
      itemsRef.current = next;
      if (mounted.current) setItems(next);
    },
    [],
  );

  useEffect(() => {
    mounted.current = true;
    const live = controllers.current;
    return () => {
      mounted.current = false;
      for (const controller of live.values()) controller.abort();
      live.clear();
    };
  }, []);

  const patch = useCallback(
    (key: string, change: Partial<AttachmentItem>) =>
      commit((prev) =>
        prev.map((i) => (i.key === key ? { ...i, ...change } : i)),
      ),
    [commit],
  );

  const upload = useCallback(
    async (key: string, file: File): Promise<void> => {
      const controller = new AbortController();
      controllers.current.set(key, controller);
      try {
        const [stored] = await uploadFiles([file], {
          signal: controller.signal,
          onProgress: (loaded, total) =>
            patch(key, { progress: total > 0 ? (loaded / total) * 100 : 0 }),
        });
        if (stored === undefined) throw new Error("empty upload answer");
        patch(key, {
          id: stored.id,
          name: stored.name,
          kind: stored.kind,
          size: stored.size,
          status: "ready",
          progress: 100,
        });
      } catch (error) {
        if (controller.signal.aborted) return;
        patch(key, { status: "error", error: describeFilesError(error) });
      } finally {
        controllers.current.delete(key);
      }
    },
    [patch],
  );

  const addFiles = useCallback(
    (files: readonly File[]) => {
      if (files.length === 0) return;
      let room = MAX_UPLOAD_FILES - itemsRef.current.length;
      let skipped = 0;
      const created: Array<{ item: AttachmentItem; file: File | null }> = [];
      for (const file of files) {
        if (room <= 0) {
          skipped += 1;
          continue;
        }
        room -= 1;
        counter.current += 1;
        const problem = checkUploadFile(file);
        created.push({
          file: problem === null ? file : null,
          item: {
            key: `att-${counter.current}`,
            id: null,
            name: file.name,
            size: file.size,
            kind: provisionalKind(file.name),
            status: problem === null ? "uploading" : "error",
            progress: 0,
            error: problem ?? undefined,
            owned: true,
          },
        });
      }
      setNotice(
        skipped > 0
          ? `Only ${MAX_UPLOAD_FILES} files fit in one message. ${skipped} ${skipped === 1 ? "file was" : "files were"} not added.`
          : null,
      );
      if (created.length === 0) return;
      commit((prev) => [...prev, ...created.map((c) => c.item)]);
      for (const { item, file } of created) {
        if (file !== null) void upload(item.key, file);
      }
    },
    [commit, upload],
  );

  const addExisting = useCallback(
    (file: UploadedFile) => {
      if (itemsRef.current.some((i) => i.id === file.id)) return;
      if (itemsRef.current.length >= MAX_UPLOAD_FILES) {
        setNotice(`Only ${MAX_UPLOAD_FILES} files fit in one message.`);
        return;
      }
      counter.current += 1;
      setNotice(null);
      commit((prev) => [
        ...prev,
        {
          key: `att-${counter.current}`,
          id: file.id,
          name: file.name,
          size: file.size,
          kind: file.kind,
          status: "ready",
          progress: 100,
          owned: false,
        },
      ]);
    },
    [commit],
  );

  const remove = useCallback(
    (key: string) => {
      const target = itemsRef.current.find((i) => i.key === key);
      if (target === undefined) return;
      controllers.current.get(key)?.abort();
      controllers.current.delete(key);
      commit((prev) => prev.filter((i) => i.key !== key));
      setNotice(null);
      if (target.owned && target.id !== null) {
        // Best effort: an orphaned upload is harmless and stays listed in Settings > Files.
        void deleteUpload(target.id).catch(() => undefined);
      }
    },
    [commit],
  );

  const clear = useCallback(() => {
    for (const controller of controllers.current.values()) controller.abort();
    controllers.current.clear();
    commit(() => []);
    setNotice(null);
  }, [commit]);

  const take = useCallback((): AttachmentRef[] => {
    const refs = itemsRef.current
      .filter((i) => i.status === "ready" && i.id !== null)
      .map((i) => ({ id: i.id as string, name: i.name, kind: i.kind }));
    clear();
    return refs;
  }, [clear]);

  const dismissNotice = useCallback(() => setNotice(null), []);

  return useMemo(
    () => ({
      items,
      uploading: items.some((i) => i.status === "uploading"),
      count: items.length,
      notice,
      dismissNotice,
      addFiles,
      addExisting,
      remove,
      take,
      clear,
    }),
    [items, notice, dismissNotice, addFiles, addExisting, remove, take, clear],
  );
}
