import React from "react";
import { configureStore } from "@reduxjs/toolkit";
import { Provider } from "react-redux";

import "@/app/globals.css";
import { ImagePickerModal } from "./ImagePickerModal";

function mountPicker(provider: string) {
  const store = configureStore({
    reducer: () => ({ userConfig: { llm_config: { IMAGE_PROVIDER: provider } } }),
  });
  cy.mount(
    <Provider store={store}>
      <ImagePickerModal open onClose={() => {}} onSelect={() => {}} />
    </Provider>,
  );
}

describe("Image picker search", () => {
  for (const provider of ["pexels", "pixabay"]) {
    it(`aligns stock search text and submits Enter once for ${provider}`, () => {
      cy.intercept("GET", "**/api/v1/ppt/images/search*", { body: [] }).as("search");
      mountPicker(provider);
      cy.wait("@search");
      const label = `Search ${provider === "pexels" ? "Pexels" : "Pixabay"} images`;
      cy.get(`textarea[aria-label="${label}"]`)
        .then(($input) => {
          const input = $input[0].getBoundingClientRect();
          const icon = $input[0].parentElement!.querySelector("svg")!.getBoundingClientRect();
          const style = getComputedStyle($input[0]);
          const firstLineCenter = input.top + parseFloat(style.paddingTop) + parseFloat(style.lineHeight) / 2;
          expect(firstLineCenter).to.be.closeTo(icon.top + icon.height / 2, 1);
        })
        .type("pine trees{enter}");
      cy.wait("@search").its("request.query.query").should("equal", "pine trees");
      cy.get("@search.all").should("have.length", 2);
      cy.get(`textarea[aria-label="${label}"]`).should("have.value", "pine trees");
    });
  }

  it("keeps multiline prompts for AI image generation", () => {
    cy.intercept("GET", "**/api/v1/ppt/images/generated", { body: [] });
    mountPicker("gpt-image-2");
    cy.get('textarea[placeholder="Describe your image"]')
      .type("A mountain{enter}at sunset")
      .should("have.value", "A mountain\nat sunset");
  });
});
