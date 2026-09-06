---
name: smart-slot-scheduling
description: Schedule consultations, demos, and follow-up calls using binary choice
  narrowing without calendar cognitive overload.
version: 1.0.0
allowed_tools:
- whatsapp_send_text_current_customer
mcp_tools: []
task_ids: []
languages:
- en
- fr
priority: 75
---
# Binary Slot Scheduling Protocol

- Never ask open-ended questions like "When are you free?" or list more than two options at once.
- The 3-Step Narrowing Funnel:
  - Step 1 (Day-Range): Offer two distinct days: "Would earlier in the week or Thursday/Friday work better for you?"
  - Step 2 (Time-Window): Offer two concrete time slots: "I have an opening Tuesday morning at 10:00 AM, or afternoon at 2:30 PM. Which one fits your schedule?"
  - Step 3 (Timezone Check): Confirm the caller's time zone if different from your operating zone.
- Lock and Confirm:
  - Once the caller picks a slot, confirm in one sentence: "You're scheduled for Tuesday at 10:00 AM. I'm sending the calendar confirmation to your WhatsApp right now."
  - Trigger whatsapp_send_text_current_customer with the meeting details.
