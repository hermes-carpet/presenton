/* eslint-disable @next/next/no-img-element */
"use client";

import React from "react";
import styles from "./TemplateCreationChrome.module.css";

const asset = (name: string) => `/custom-template/${name}`;

export function TemplateCreationTitle({ activeStep }: { activeStep: 1 | 2 | 3 }) {
  return (
    <div className="px-4 pt-12 text-center font-syne sm:pt-[66px]">
      <h1 className="text-[34px] font-medium leading-[1.2] tracking-[-0.58px] text-[#101323] sm:text-[48px] lg:text-[58px]">Create a New Template</h1>
      <p className="mx-auto mt-1.5 max-w-[635px] text-base leading-[1.4] tracking-[0.2px] text-[#101323CC] sm:text-xl">Turn your PowerPoint into a reusable AI presentation template.</p>
      <nav aria-label="Template creation progress" className="mt-7 flex items-center justify-center gap-[5px]">
        {["Upload", "Analyze", "Preview"].map((label, index) => (
          <React.Fragment key={label}>
            {index > 0 && <img src={asset("steps-imgVector63.svg")} alt="" />}
            <div aria-current={activeStep === index + 1 ? "step" : undefined} className="flex items-center gap-1">
              <span className={`flex h-7 w-7 items-center justify-center rounded-full border text-xs [font-family:var(--font-manrope)] ${activeStep === index + 1 ? "border-[#010100] bg-[#010100] text-white" : "border-[#ECECEF] text-[#494A4D]"}`}>{index + 1}</span>
              <span className="text-[11.61px] text-[#010000]">{label}</span>
            </div>
          </React.Fragment>
        ))}
      </nav>
    </div>
  );
}

export function UploadIllustration() {
  return (
    <div aria-hidden="true" className="relative h-[77.416px] w-[120.95px] shrink-0">
      {[-7, 7].map((rotation) => <div key={rotation} className="absolute left-[3.475px] top-[6.708px] h-16 w-[114px] overflow-hidden rounded-md border border-[#EDEEEF] bg-white" style={{ transform: `rotate(${rotation}deg)` }}><img src={asset("upload-imgImage407.png")} alt="" className="absolute left-[29px] top-[9px] h-[45px] w-[49px] object-cover" /></div>)}
      <img src={asset("upload-imgRectangle4027.svg")} alt="" className="absolute left-[2.98px] top-[5px] max-w-none" />
      <img src={asset("upload-imgRectangle4028.svg")} alt="" className="absolute left-[-0.02px] top-[6px] max-w-none" />
      <div className="absolute left-[3.47px] top-[5.71px] h-16 w-[114px] overflow-hidden rounded-md">
        <img src={asset("upload-imgImage407.png")} alt="" className="absolute left-[30px] top-[10px] h-[45px] w-[49px] object-cover" />
        <div className="absolute right-[-1.45px] top-[0.31px] flex h-[24.012px] w-[23.985px] items-center justify-center">
          <div className="relative h-[20.506px] w-[18.79px] rotate-[103.21deg] skew-x-[-2.99deg]">
            <img src={asset("upload-imgPolygon4.svg")} alt="" className="absolute left-[-2.142px] top-[-0.269px] max-w-none" />
            <img src={asset("upload-imgPolygon5.svg")} alt="" className="absolute left-[-2.142px] top-[-0.269px] max-w-none" />
          </div>
        </div>
      </div>
    </div>
  );
}

export function AttachmentButton({ onClick, disabled = false }: { onClick: () => void; disabled?: boolean }) {
  return <button type="button" onClick={onClick} disabled={disabled} aria-label="Choose PowerPoint file" className="flex h-[34px] w-[42px] shrink-0 items-center justify-center rounded-full border border-[#EDEEEF] bg-white hover:bg-[#F9F9FA] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7A5AF8] disabled:opacity-50"><img src={asset("upload-imgFrame.svg")} alt="" /></button>;
}

export function ContinueButton({ onClick, disabled, label }: { onClick: () => void; disabled?: boolean; label: string }) {
  return <button type="button" onClick={onClick} disabled={disabled} aria-label={label} className="flex h-10 w-[60px] shrink-0 items-center justify-center rounded-full bg-[#7A5AF8] hover:bg-[#6947EB] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7A5AF8] focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"><img src={asset("upload-imgFrame3.svg")} alt="" /></button>;
}

export function ProcessingStatus({ label }: { label: string }) {
  return <span role="status" className="flex items-center gap-1.5 text-sm text-[#333] sm:text-base"><span aria-hidden="true" className={styles.loaderContainer}><span className={styles.loader} /></span>{label}</span>;
}
