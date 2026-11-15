import { useRouter } from "next/navigation";

/**
 * The app router when mounted, else a fallback that does a plain page navigation. Menus use it so
 * a view can be rendered (and tested) outside the Next app router.
 */
export function useOptionalRouter(): { push: (href: string) => void } {
  try {
    return useRouter();
  } catch {
    return {
      push: (href: string): void => {
        window.location.assign(href);
      },
    };
  }
}
