"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import type { GenerationMode, PresentationGenerationMode } from "@/utils/presentationGenerationMode";
import { isGenerationModeAvailable } from "@/utils/presentationGenerationMode";
import { X } from "lucide-react";

const modes = [
  { id: "standard" as const, title: "YOUR CONTENT, YOUR TEMPLATE", label: "Standard mode", video: "/Standard.mp4", description: "places your text and images into the template you choose. The slide layout stays consistent as your content changes.", features: ["Uses built-in or custom templates", "Follows the template’s fixed layout"] },
  { id: "smart" as const, title: "DESIGNED AROUND YOUR CONTENT", label: "Smart mode", video: "/Smart.mp4", description: "arranges your text and images to suit each slide. The layout can change depending on what you want to present.", features: ["Creates layouts around your content", "Adapts text and image placement"] },
];

export default function GenerationModeDialog({ open, onOpenChange, onSelect, availableMode = "both" }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (mode: GenerationMode) => void;
  availableMode?: PresentationGenerationMode;
}) {
  const [activeMode, setActiveMode] = useState<GenerationMode | null>(null);
  const videos = useRef<Partial<Record<GenerationMode, HTMLVideoElement | null>>>({});

  useEffect(() => {
    for (const mode of modes) {
      const video = videos.current[mode.id];
      if (!video) continue;
      if (!open || (activeMode !== null && activeMode !== mode.id)) video.pause();
      else void video.play().catch(() => {});
    }
  }, [activeMode, open]);

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => { setActiveMode(null); onOpenChange(nextOpen); }}>
      <DialogContent hideDefaultClose overlayClassName="bg-black/30 backdrop-blur-none" className="max-h-[calc(100dvh-32px)] w-[calc(100%-32px)] max-w-[820px] gap-5 overflow-y-auto border-0 bg-transparent p-0 shadow-none sm:rounded-none">
        <DialogTitle className="sr-only">Choose a presentation mode</DialogTitle>
        <DialogDescription className="sr-only">Compare Standard and Smart previews, then select a mode. Hover or focus a card to pause the other preview.</DialogDescription>
        <DialogClose aria-label="Close presentation modes" className="absolute right-2 top-2 z-10 flex h-8 w-8 items-center justify-center rounded-full bg-white/90 text-[#666666] hover:bg-white focus-visible:ring-2 focus-visible:ring-[#7A5AF8]"><X className="h-4 w-4" /></DialogClose>
        <div className="grid gap-5 md:grid-cols-2" onMouseLeave={() => setActiveMode(null)} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setActiveMode(null); }}>
          {modes.map((mode) => {
            const dimmed = activeMode !== null && activeMode !== mode.id;
            const available = isGenerationModeAvailable(availableMode, mode.id);
            return (
              <button key={mode.id} type="button" disabled={!available} aria-label={`Select ${mode.label}`} onMouseEnter={() => setActiveMode(mode.id)} onFocus={() => setActiveMode(mode.id)} onClick={() => { setActiveMode(null); onSelect(mode.id); onOpenChange(false); }} className={`overflow-hidden rounded-xl bg-white text-left transition-shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7A5AF8] disabled:cursor-not-allowed disabled:opacity-40 ${activeMode === mode.id ? "shadow-[0_4px_18px_rgba(0,0,0,0.37)]" : ""}`}>
                <div className={`transition-opacity ${dimmed ? "opacity-70" : "opacity-100"}`}>
                  <video ref={(element) => { videos.current[mode.id] = element; }} src={mode.video} autoPlay muted loop playsInline aria-label={`${mode.label} preview`} className="aspect-[4/3] w-full object-cover" />
                  <div className="space-y-[18px] p-[22px] font-manrope">
                    <p className="text-xs font-semibold tracking-[1.2px] text-[#666666]">{mode.title}</p>
                    <p className="text-sm font-medium leading-[22px] tracking-[0.28px] text-[#333333]">
                      <span className={`mr-2 inline-block rounded-full px-3 py-0.5 text-xs font-semibold leading-4 ${mode.id === "smart" ? "bg-[#EFEDFE] text-[#5F48F3]" : "bg-[#F4FBFE] text-[#01A8F2]"}`}>{mode.label}</span>
                      {mode.description}
                    </p>
                    <ul className="divide-y divide-[#EDEEEF] font-syne text-[13px] leading-5 text-[#333333]">
                      {mode.features.map((feature) => <li key={feature} className="flex items-center gap-1 py-2 first:pt-0 last:pb-0"><Image src={`/generate/${mode.id}-check.svg`} alt="" width={14} height={14} />{feature}</li>)}
                    </ul>
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </DialogContent>
    </Dialog>
  );
}
