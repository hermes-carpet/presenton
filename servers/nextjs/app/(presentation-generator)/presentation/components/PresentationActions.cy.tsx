import React, { StrictMode, useState } from "react";
import { Provider } from "react-redux";
import { configureStore } from "@reduxjs/toolkit";

import "@/app/globals.css";
import PresentationActions from "./PresentationActions";
import type { ChatApiAdapter } from "./chat/chat-types";
import type { ChatConversationSummary } from "../../services/api/chat";
import { PRESENTON_BLANK_SLIDE_PROMPT_EVENT } from "../../_shared/blank-slide-prompt-event";

function Harness({ adapter }: { adapter: ChatApiAdapter }) {
  const [open, setOpen] = useState(false);
  const [store] = useState(() => configureStore({
    reducer: () => ({
      userConfig: { llm_config: { LLM: "openai", OPENAI_MODEL: "gpt-4o" } },
      presentationGeneration: { chatHtmlSelection: null },
    }),
  }));

  return (
    <Provider store={store}>
      <button data-cy="toggle-panel" onClick={() => setOpen((current) => !current)}>
        Toggle assistant
      </button>
      <div data-cy="tools" data-open={open} style={{ width: 383, height: 720 }}>
        <PresentationActions
          presentationId="blank-prompt-test"
          presentationType="standard"
          variant="template-v2"
          currentSlide={1}
          presentationData={{ slides: [{ id: "s0", index: 0 }, { id: "s1", index: 1 }], template_id: "blank" }}
          panelOpen={open}
          onPanelOpenChange={setOpen}
          chatAdapter={adapter}
        />
      </div>
    </Provider>
  );
}

function submitBlankPrompt(prompt: string) {
  // StrictMode can defer the initial commit until after cy.mount resolves.
  cy.get('[aria-label="Editor tools"]').should("be.visible");
  cy.window().then((window) => {
    window.dispatchEvent(new window.CustomEvent(PRESENTON_BLANK_SLIDE_PROMPT_EVENT, {
      detail: { prompt, slideIndex: 1, promptKind: "blank" },
    }));
  });
}

describe("Blank slide prompt handoff", () => {
  it("opens the closed assistant, waits for history and submits only once", () => {
    let releaseHistory: (history: ChatConversationSummary[]) => void;
    const history = new Promise<ChatConversationSummary[]>((resolve) => {
      releaseHistory = resolve;
    });
    const streamMessage = cy.stub().resolves({ response: "Done", tool_calls: [] }).as("sendPrompt");
    const adapter: ChatApiAdapter = {
      listConversations: () => history,
      getHistory: async () => ({ messages: [] }),
      deleteConversation: async () => {},
      streamMessage,
    };

    cy.mount(<StrictMode><Harness adapter={adapter} /></StrictMode>);
    submitBlankPrompt("Create a slide about solar power");
    cy.get('[data-cy="tools"]').should("have.attr", "data-open", "true");
    cy.get("@sendPrompt").should("not.have.been.called");
    cy.then(() => releaseHistory([]));
    cy.get("@sendPrompt").should("have.been.calledOnce").then(() => {
      const payload = streamMessage.firstCall.args[0];
      expect(payload.message).to.include("Create a slide about solar power");
      expect(payload.message).to.include("existing selected slide 2");
      expect(payload.message).to.include("do not add another slide");
    });

    cy.contains("Done").should("exist");
    cy.get('[data-cy="toggle-panel"]').click().click();
    cy.get("textarea").should("exist");
    cy.get("@sendPrompt").should("have.been.calledOnce");
  });

  it("switches an open insert panel to AI and submits the prompt once", () => {
    const streamMessage = cy.stub().resolves({ response: "Done", tool_calls: [] }).as("sendPrompt");
    const adapter: ChatApiAdapter = {
      listConversations: async () => [],
      getHistory: async () => ({ messages: [] }),
      deleteConversation: async () => {},
      streamMessage,
    };

    cy.mount(<Harness adapter={adapter} />);
    cy.get('[aria-label="Editor tools"]').contains("button", "Texts").click();
    cy.contains("Text Styles").should("be.visible");
    submitBlankPrompt("Create a slide about wind power");
    cy.get('[aria-label="Editor tools"]').contains("button", "AI")
      .should("have.attr", "aria-pressed", "true");
    cy.get("@sendPrompt").should("have.been.calledOnce");
  });
});
