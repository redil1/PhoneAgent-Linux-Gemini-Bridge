---
name: omnichannel-whatsapp-delivery
description: Orchestrate live in-call WhatsApp message and checkout link delivery,
  confirming receipt while keeping caller on the line.
version: 1.0.0
allowed_tools:
- whatsapp_send_text_current_customer
- whatsapp_last_delivery_status
mcp_tools: []
task_ids: []
languages:
- en
- fr
priority: 80
---
# Omnichannel WhatsApp Delivery Protocol

- When the caller authorizes receiving a checkout link, contract, or brochure:
  - Announce the dispatch naturally before or during tool execution: "I am sending the formal agreement and checkout link to your WhatsApp on this number right now."
- Use whatsapp_send_text_current_customer to dispatch the link or message.
- Verify delivery status using whatsapp_last_delivery_status if needed.
- Conversational Receipt Confirmation:
  - Ask: "Did you hear that notification chime on your phone?" or "Let me know when that arrives on your WhatsApp."
  - Wait for caller confirmation before ending the call or transitioning to the next topic.
- Fallback Handling: If the caller does not use WhatsApp or reports delivery issues, offer an email alternative smoothly.
