import { useState } from "react";
import { mergeAttributes } from "@tiptap/core";
import { DOMSerializer } from "@tiptap/pm/model";
import { TiptapInlineTextEditor } from "@/components/slide-editor/text/TiptapInlineTextEditor";
import type { TextRun } from "@/components/slide-editor/types";

const DEFAULT_RUNS: TextRun[] = [
  { text: "Energy: ", font: { family: "Arial", size: 24 } },
  { type: "latex", latex: "E=mc^2", font: { family: "Arial", size: 24 } },
];

function InlineHarness({ initialRuns = DEFAULT_RUNS }: { initialRuns?: TextRun[] }) {
  const [runs, setRuns] = useState<TextRun[]>(initialRuns);
  return <>
    <TiptapInlineTextEditor autoFocus={false} baseFont={{ family: "Arial", size: 24 }} editorStyle={{ fontSize: 24 }} runs={runs}
      onRunsChange={setRuns} onBlurOutside={() => {}} onCommitShortcut={() => {}} onEscape={() => {}} onSelectionChange={() => {}} />
    <output data-cy="runs">{JSON.stringify(runs)}</output>
  </>;
}

describe("Secure editor compatibility", () => {
  it("does not copy inherited event handlers into serialized DOM", () => {
    cy.document().then((document) => {
      const attributes = mergeAttributes(JSON.parse('{"alt":"safe","__proto__":{"src":"x-invalid://canary","onerror":"alert(1)"}}'));
      const image = DOMSerializer.renderSpec(document, ["img", attributes]).dom as HTMLImageElement;
      expect(image.alt).to.equal("safe");
      expect(image.hasAttribute("src")).to.equal(false);
      expect(image.hasAttribute("onerror")).to.equal(false);
    });
  });

  it("preserves inline text updates, undo and formatting", () => {
    cy.mount(<InlineHarness initialRuns={[{ text: "Original text", font: { family: "Arial", size: 24 } }]} />);
    cy.get("[contenteditable=true]").as("editor");
    cy.get("@editor").click().type("{moveToEnd}!");
    cy.get('[data-cy="runs"]').should("contain.text", "Original text!");
    cy.get("@editor").type(Cypress.platform === "darwin" ? "{meta}z" : "{ctrl}z");
    cy.get('[data-cy="runs"]').should("contain.text", "Original text").and("not.contain.text", "Original text!");
    cy.get("@editor").type("{selectAll}").type(Cypress.platform === "darwin" ? "{meta}b" : "{ctrl}b");
    cy.get("@editor").find("strong").should("have.text", "Original text");
    cy.get('[data-cy="runs"]').should("contain.text", '"bold":true');
  });

  it("preserves editable LaTeX atoms and text-run updates", () => {
    cy.mount(<InlineHarness />);
    cy.get('textarea[aria-label="Edit LaTeX expression"]').should("have.value", "E=mc^2")
      .clear().type("a^2+b^2=c^2", { parseSpecialCharSequences: false });
    cy.get('[data-cy="runs"]').should("contain.text", "a^2+b^2=c^2").and("contain.text", "Energy: ");
  });
});
