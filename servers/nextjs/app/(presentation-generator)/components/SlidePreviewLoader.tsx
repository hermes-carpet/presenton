import { LoaderCircle } from "lucide-react";

export default function SlidePreviewLoader({
  label = "Loading slide…",
}: {
  label?: string;
}) {
  return (
    <div
      role="status"
      className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-white text-[#6847F4]"
    >
      <LoaderCircle
        aria-hidden="true"
        className="h-6 w-6 animate-spin motion-reduce:animate-none"
        strokeWidth={1.75}
      />
      <span className="font-manrope text-xs text-[#667085]">{label}</span>
    </div>
  );
}
