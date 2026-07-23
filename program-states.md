**MTGNP RFC v3**

Sample PDU Exchange — LOBBY \+ GAME\_SETUP \+ MULLIGAN \+ IN\_GAME \+ GAME\_OVER

# **1\.  LOBBY State**

Both players connect and declare their decks. The server waits until it has received a valid PLAYER\_READY from each player before advancing.

## **Step 1 \- Player 1 sends PLAYER\_READY**

**C \-\> S**

| {   "type":      "PLAYER\_READY",   "seq\_num":   1,   "player\_id": "player\_1",   "deck\_list": \[     "lightning\_bolt\_001", "lightning\_bolt\_002", "lightning\_bolt\_003",     "shock\_001",          "shock\_002",     "goblin\_guide\_001",     "mountain\_001",       "mountain\_002"   \] } |
| :---- |

## **Step 2 \- Server acknowledges, waits for Player 2**

**S \-\> P1**

| {   "type":    "GAME\_STATE\_UPDATE",   "seq\_num": 1,   "state": {     "phase":         "LOBBY",     "players\_ready": 1,     "waiting\_for":   \["player\_2"\]   } } |
| :---- |

## **Step 3 \- Player 2 sends PLAYER\_READY**

**C \-\> S**

| {   "type":      "PLAYER\_READY",   "seq\_num":   1,   "player\_id": "player\_2",   "deck\_list": \[     "counterspell\_001",  "counterspell\_002",     "gray\_merchant\_001", "gray\_merchant\_002",     "island\_001",        "island\_002",     "swamp\_001",         "swamp\_002"   \] } |
| :---- |

## **Step 4 \- Server confirms both ready, transitions to GAME\_SETUP**

**S \-\> ALL**

| {   "type":    "GAME\_STATE\_UPDATE",   "seq\_num": 2,   "state": {     "phase":         "GAME\_SETUP",     "players\_ready": 2,     "waiting\_for":   \[\]   } } |
| :---- |

# **2\.  GAME\_SETUP State**

GAME\_SETUP is fully automatic — no client input is required. The server validates decks, sets life totals to 20, shuffles each deck, draws seven cards per player, and determines who goes first via coin flip. It then broadcasts a personalized GAME\_STATE\_UPDATE to each player before transitioning to MULLIGAN.

## **Step 5 \- Server sends personalized GAME\_STATE\_UPDATE to Player 1**

Player 1's hand is visible to them; Player 2's hand is hidden (only the count is shown).

**S \-\> P1**

| {   "type":    "GAME\_STATE\_UPDATE",   "seq\_num": 3,   "state": {     "turn": 0, "phase": "MULLIGAN", "active\_player": "player\_1",     "life\_totals": { "player\_1": 20, "player\_2": 20 },     "hand": \["lightning\_bolt\_001","shock\_001","mountain\_001","mountain\_002","goblin\_guide\_001","lightning\_bolt\_002","mountain\_003"\],     "hand\_counts": { "player\_2": 7 }, "library\_counts": { "player\_1": 1, "player\_2": 1 },     "battlefield": { "player\_1": \[\], "player\_2": \[\] },     "graveyard":   { "player\_1": \[\], "player\_2": \[\] }, "stack": \[\]   } } |
| :---- |

## **Step 6 \- Server sends personalized GAME\_STATE\_UPDATE to Player 2**

Player 2's hand is visible to them; Player 1's hand is hidden (only the count is shown).

**S \-\> P2**

| {   "type":    "GAME\_STATE\_UPDATE",   "seq\_num": 3,   "state": {     "turn": 0, "phase": "MULLIGAN", "active\_player": "player\_1",     "life\_totals": { "player\_1": 20, "player\_2": 20 },     "hand": \["counterspell\_001","gray\_merchant\_001","island\_001","swamp\_001","counterspell\_002","gray\_merchant\_002","swamp\_002"\],     "hand\_counts": { "player\_1": 7 }, "library\_counts": { "player\_1": 1, "player\_2": 1 },     "battlefield": { "player\_1": \[\], "player\_2": \[\] },     "graveyard":   { "player\_1": \[\], "player\_2": \[\] }, "stack": \[\]   } } |
| :---- |

# **3\.  MULLIGAN State**

Each player independently decides whether to keep their opening hand or take a mulligan. MTGNP uses the London Mulligan rule: a player who mulligans draws a new hand of seven cards, then puts a number of cards on the bottom of their library equal to the number of times they have mulliganed.

In this example, Player 1 keeps their opening hand immediately, while Player 2 takes one mulligan before keeping — and must therefore bottom exactly 1 card.

## **Step 7 \- Player 1 keeps their opening hand**

Player 1 is satisfied with their hand. seq\_num echoes the GAME\_STATE\_UPDATE from Step 5 (seq\_num 3). No cards are bottomed since Player 1 has not mulliganed.[^1]

**C \-\> S  (Player 1\)**

| {   "type": "MULLIGAN\_CHOICE", "seq\_num": 3, "keep": true, "cards\_to\_bottom": \[\] } |
| :---- |

## **Step 8 \- Player 2 takes a mulligan**

Player 2 is not happy with their opening hand. seq\_num echoes the GAME\_STATE\_UPDATE from Step 6 (seq\_num 3). cards\_to\_bottom is empty when keep is false.

**C \-\> S  (Player 2\)**

| {   "type": "MULLIGAN\_CHOICE", "seq\_num": 3, "keep": false, "cards\_to\_bottom": \[\] } |
| :---- |

## **Step 9 \- Server redraws 7 cards for Player 2**

The server draws a fresh 7-card hand for Player 2 and sends a new personalized GAME\_STATE\_UPDATE. seq\_num advances to 4\. Player 1 does not receive a new update.[^2]

**S \-\> P2**

| {   "type":    "GAME\_STATE\_UPDATE",   "seq\_num": 4,   "state": {     "turn": 0, "phase": "MULLIGAN", "active\_player": "player\_1",     "life\_totals": { "player\_1": 20, "player\_2": 20 },     "hand": \["counterspell\_001","island\_001","swamp\_001","island\_002","gray\_merchant\_001","swamp\_002","counterspell\_002"\],     "hand\_counts": { "player\_1": 7 }, "library\_counts": { "player\_1": 1, "player\_2": 1 },     "battlefield": { "player\_1": \[\], "player\_2": \[\] },     "graveyard":   { "player\_1": \[\], "player\_2": \[\] }, "stack": \[\]   } } |
| :---- |

## **Step 10 \- Player 2 keeps after mulligan, bottoms 1 card**

Player 2 keeps the new hand. Because they mulliganed once, cards\_to\_bottom MUST contain exactly 1 card ID. seq\_num echoes the redraw GAME\_STATE\_UPDATE from Step 9 (seq\_num 4\).[^3]

**C \-\> S  (Player 2\)**

| {   "type": "MULLIGAN\_CHOICE", "seq\_num": 4, "keep": true, "cards\_to\_bottom": \["counterspell\_002"\] } |
| :---- |

## **Step 11 \- Both players have kept; server transitions to IN\_GAME**

Both players have now sent MULLIGAN\_CHOICE with keep: true. The server transitions to IN\_GAME and begins Player 1's first turn, broadcasting a PHASE\_TRANSITION to all players.[^4]

seq\_num 5 on the PHASE\_TRANSITION continues the server counter from the last GAME\_STATE\_UPDATE sent to Player 2 (seq\_num 4\).[^5]

**S \-\> ALL**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 5,   "from\_phase": "MULLIGAN", "to\_phase": "UNTAP",   "active\_player": "player\_1", "turn": 1 } |
| :---- |

# **4\.  IN\_GAME State — Turn 1 (Player 1\)**

Player 1 is the Active Player (AP). Player 2 is the Non-Active Player (NAP). The turn follows the full phase sequence: Untap \-\> Upkeep \-\> Draw \-\> Precombat Main \-\> Combat \-\> Postcombat Main \-\> End Step \-\> Cleanup.

## **Step 12 \- Untap Step (automatic, no priority)**

The server untaps all of Player 1's permanents and resets land\_played\_this\_turn to false. No priority is granted. The server immediately advances to Upkeep.[^6]

**S \-\> ALL**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 6,   "from\_phase": "MULLIGAN", "to\_phase": "UNTAP",   "active\_player": "player\_1", "turn": 1 } |
| :---- |

**S \-\> ALL  (untap broadcast)**

| {   "type": "GAME\_STATE\_UPDATE", "seq\_num": 7,   "state": {     "turn": 1, "phase": "UNTAP", "active\_player": "player\_1",     "life\_totals": { "player\_1": 20, "player\_2": 20 }, "land\_played": false,     "battlefield": { "player\_1": \[\], "player\_2": \[\] },     "hand\_counts": { "player\_1": 7, "player\_2": 6 },     "library\_counts": { "player\_1": 1, "player\_2": 1 }, "stack": \[\]   } } |
| :---- |

**S \-\> ALL  (advance to Upkeep)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 8,   "from\_phase": "UNTAP", "to\_phase": "UPKEEP",   "active\_player": "player\_1", "turn": 1 } |
| :---- |

## **Step 13 \- Upkeep Step (both players pass, no actions)**

The server opens a priority window. Player 1 holds priority first. Both players pass with an empty stack, so the server advances to Draw.[^7]

**S \-\> P1**

| {   "type": "PRIORITY\_GRANT", "player\_id": "player\_1", "seq\_num": 8, "time\_limit\_ms": 60000 } |
| :---- |

**C \-\> S  (Player 1 passes)**

| {   "type": "PRIORITY\_PASS", "seq\_num": 8 } |
| :---- |

**S \-\> P2**

| {   "type": "PRIORITY\_GRANT", "player\_id": "player\_2", "seq\_num": 9, "time\_limit\_ms": 60000 } |
| :---- |

**C \-\> S  (Player 2 passes)**

| {   "type": "PRIORITY\_PASS", "seq\_num": 9 } |
| :---- |

**S \-\> ALL  (both passed, empty stack — advance to Draw)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 10,   "from\_phase": "UPKEEP", "to\_phase": "DRAW",   "active\_player": "player\_1", "turn": 1 } |
| :---- |

## **Step 14 \- Draw Step (Player 1 draws, both pass)**

The server draws one card for Player 1 and sends a personalized GAME\_STATE\_UPDATE. A priority window opens; both players pass and the server advances to Precombat Main.[^8]

**S \-\> P1  (shock\_002 added to hand)**

| {   "type": "GAME\_STATE\_UPDATE", "seq\_num": 11,   "state": {     "turn": 1, "phase": "DRAW", "active\_player": "player\_1",     "life\_totals": { "player\_1": 20, "player\_2": 20 },     "hand": \["lightning\_bolt\_001","shock\_001","mountain\_001","mountain\_002","goblin\_guide\_001","lightning\_bolt\_002","mountain\_003","shock\_002"\],     "hand\_counts": { "player\_2": 6 },     "library\_counts": { "player\_1": 0, "player\_2": 1 }, "stack": \[\]   } } |
| :---- |

Priority exchange follows (Player 1 passes, Player 2 passes, empty stack). Server advances.

**S \-\> ALL  (advance to Precombat Main)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 14,   "from\_phase": "DRAW", "to\_phase": "PRECOMBAT\_MAIN",   "active\_player": "player\_1", "turn": 1 } |
| :---- |

## **Step 15 \- Precombat Main Phase: Play a Land**

Player 1 plays mountain\_003 as their land for the turn. Playing a land does not use the stack and does not require priority. The server updates state and re-issues PRIORITY\_GRANT to Player 1.[^9]

**C \-\> S  (Player 1 plays land)**

| {   "type": "PLAY\_LAND", "seq\_num": 14, "card\_id": "mountain\_003" } |
| :---- |

**S \-\> ALL  (land enters battlefield)**

| {   "type": "GAME\_STATE\_UPDATE", "seq\_num": 15,   "state": {     "turn": 1, "phase": "PRECOMBAT\_MAIN", "active\_player": "player\_1",     "life\_totals": { "player\_1": 20, "player\_2": 20 }, "land\_played": true,     "battlefield": { "player\_1": \[{ "id": "mountain\_003", "tapped": false }\], "player\_2": \[\] },     "hand\_counts": { "player\_1": 7, "player\_2": 6 }, "stack": \[\]   } } |
| :---- |

**S \-\> P1  (re-issue priority after land)**

| {   "type": "PRIORITY\_GRANT", "player\_id": "player\_1", "seq\_num": 15, "time\_limit\_ms": 60000 } |
| :---- |

## **Step 16 \- Precombat Main Phase: Cast Goblin Guide**

Player 1 casts goblin\_guide\_001, paying 1 Red mana. Both players pass priority consecutively, the spell resolves, and Goblin Guide enters the battlefield.

**C \-\> S  (Player 1 casts Goblin Guide)**

| {   "type": "CAST\_SPELL", "seq\_num": 15,   "card\_id": "goblin\_guide\_001", "targets": \[\], "mana\_payment": { "R": 1 } } |
| :---- |

**S \-\> ALL  (spell pushed to stack)**

| {   "type": "STACK\_PUSH", "seq\_num": 16,   "stack\_item\_id": "stk\_01", "item\_type": "SPELL",   "source": "goblin\_guide\_001", "targets": \[\], "controller": "player\_1" } |
| :---- |

**S \-\> P1  (AP retains priority)**

| {   "type": "PRIORITY\_GRANT", "player\_id": "player\_1", "seq\_num": 16, "time\_limit\_ms": 60000 } |
| :---- |

**C \-\> S  (Player 1 passes)**

| {   "type": "PRIORITY\_PASS", "seq\_num": 16 } |
| :---- |

**S \-\> P2**

| {   "type": "PRIORITY\_GRANT", "player\_id": "player\_2", "seq\_num": 17, "time\_limit\_ms": 60000 } |
| :---- |

**C \-\> S  (Player 2 passes — no response)**

| {   "type": "PRIORITY\_PASS", "seq\_num": 17 } |
| :---- |

Both players passed consecutively with a non-empty stack — server resolves the top item.

**S \-\> ALL  (Goblin Guide resolves, enters battlefield)**

| {   "type": "STACK\_RESOLVE", "seq\_num": 18,   "stack\_item\_id": "stk\_01", "result": "RESOLVED",   "state\_changes": \[{ "type": "PERMANENT\_ENTERS", "card\_id": "goblin\_guide\_001",     "controller": "player\_1", "tapped": false }\] } |
| :---- |

**S \-\> ALL  (updated battlefield)**

| {   "type": "GAME\_STATE\_UPDATE", "seq\_num": 19,   "state": {     "turn": 1, "phase": "PRECOMBAT\_MAIN", "active\_player": "player\_1",     "life\_totals": { "player\_1": 20, "player\_2": 20 },     "battlefield": { "player\_1": \[         { "id": "mountain\_003", "tapped": false },         { "id": "goblin\_guide\_001", "tapped": false, "summoning\_sickness": true }       \], "player\_2": \[\] },     "hand\_counts": { "player\_1": 6, "player\_2": 6 }, "stack": \[\]   } } |
| :---- |

Both players pass priority again with an empty stack. Server advances to Combat.

**S \-\> ALL  (advance to Combat)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 22,   "from\_phase": "PRECOMBAT\_MAIN", "to\_phase": "BEGIN\_COMBAT",   "active\_player": "player\_1", "turn": 1 } |
| :---- |

## **Step 17 \- Begin Combat Step (both pass)**

Priority window opens at Begin Combat. Both players pass with an empty stack.

**S \-\> ALL  (advance to Declare Attackers)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 25,   "from\_phase": "BEGIN\_COMBAT", "to\_phase": "DECLARE\_ATTACKERS",   "active\_player": "player\_1", "turn": 1 } |
| :---- |

## **Step 18 \- Declare Attackers Step**

Goblin Guide entered the battlefield this turn, so it has summoning sickness and MUST NOT attack. Player 1 has no other attackers, so they declare no attackers. The server advances past combat.[^10]

**S \-\> P1  (priority to declare attackers)**

| {   "type": "PRIORITY\_GRANT", "player\_id": "player\_1", "seq\_num": 25, "time\_limit\_ms": 60000 } |
| :---- |

**C \-\> S  (Player 1 declares no attackers)**

| {   "type": "DECLARE\_ATTACKERS", "seq\_num": 25, "attackers": \[\] } |
| :---- |

With no attackers declared, the server skips Declare Blockers, Assign Damage Order, and Combat Damage, advancing directly to End of Combat.

**S \-\> ALL  (skip to End of Combat)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 26,   "from\_phase": "DECLARE\_ATTACKERS", "to\_phase": "END\_OF\_COMBAT",   "active\_player": "player\_1", "turn": 1 } |
| :---- |

## **Step 19 \- Postcombat Main Phase (both pass)**

Priority window opens. Player 1 takes no further actions. Both pass with empty stack.

**S \-\> ALL  (advance to Postcombat Main)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 29,   "from\_phase": "END\_OF\_COMBAT", "to\_phase": "POSTCOMBAT\_MAIN",   "active\_player": "player\_1", "turn": 1 } |
| :---- |

Both players pass priority. Server advances to End Step.

**S \-\> ALL**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 32,   "from\_phase": "POSTCOMBAT\_MAIN", "to\_phase": "END\_STEP",   "active\_player": "player\_1", "turn": 1 } |
| :---- |

## **Step 20 \- End Step (both pass)**

A final priority window opens. Both players pass with an empty stack. Server advances to Cleanup.

**S \-\> ALL**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 35,   "from\_phase": "END\_STEP", "to\_phase": "CLEANUP",   "active\_player": "player\_1", "turn": 1 } |
| :---- |

## **Step 21 \- Cleanup Step**

Player 1 has 6 cards in hand (under the 7-card limit), so no discard is needed. The server clears all damage from creatures and removes until-end-of-turn effects, then broadcasts a final GAME\_STATE\_UPDATE. Summoning sickness is cleared from Goblin Guide. No priority is granted. The server increments the turn counter, switches the Active Player to Player 2, and begins Turn 2's Untap Step.

**S \-\> ALL  (damage cleared, summoning sickness removed)**

| {   "type": "GAME\_STATE\_UPDATE", "seq\_num": 36,   "state": {     "turn": 1, "phase": "CLEANUP", "active\_player": "player\_1",     "life\_totals": { "player\_1": 20, "player\_2": 20 },     "battlefield": { "player\_1": \[         { "id": "mountain\_003", "tapped": false },         { "id": "goblin\_guide\_001", "tapped": false, "summoning\_sickness": false }       \], "player\_2": \[\] },     "hand\_counts": { "player\_1": 6, "player\_2": 6 }, "stack": \[\]   } } |
| :---- |

**S \-\> ALL  (Turn 2 begins — Player 2 is now Active Player)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 37,   "from\_phase": "CLEANUP", "to\_phase": "UNTAP",   "active\_player": "player\_2", "turn": 2 } |
| :---- |

# **5\.  IN\_GAME State — Turn 2 (Player 2\)**

Player 2 is now the Active Player (AP). Player 1 is the Non-Active Player (NAP). Player 2 enters with 6 cards in hand, no permanents on the battlefield. Player 1 has mountain\_003 and goblin\_guide\_001 in play. The Turn 2 UNTAP PHASE\_TRANSITION was already broadcast at seq\_num 37\.

## **Step 22 \- Untap Step (automatic, no priority)**

Player 2 has no permanents to untap. The server resets land\_played\_this\_turn for Player 2 and immediately advances to Upkeep.

**S \-\> ALL  (state after untap)**

| {   "type": "GAME\_STATE\_UPDATE", "seq\_num": 38,   "state": {     "turn": 2, "phase": "UNTAP", "active\_player": "player\_2",     "life\_totals": { "player\_1": 20, "player\_2": 20 }, "land\_played": false,     "battlefield": { "player\_1": \[         { "id": "mountain\_003", "tapped": false },         { "id": "goblin\_guide\_001", "tapped": false }       \], "player\_2": \[\] },     "hand\_counts": { "player\_1": 6, "player\_2": 6 },     "library\_counts": { "player\_1": 0, "player\_2": 1 }, "stack": \[\]   } } |
| :---- |

**S \-\> ALL  (advance to Upkeep)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 39,   "from\_phase": "UNTAP", "to\_phase": "UPKEEP",   "active\_player": "player\_2", "turn": 2 } |
| :---- |

## **Step 23 \- Upkeep Step (both pass)**

Priority opens with Player 2 holding priority first. Neither player takes action. Both pass consecutively with an empty stack.

**S \-\> P2**

| {   "type": "PRIORITY\_GRANT", "player\_id": "player\_2", "seq\_num": 39, "time\_limit\_ms": 60000 } |
| :---- |

**C \-\> S  (Player 2 passes)**

| {   "type": "PRIORITY\_PASS", "seq\_num": 39 } |
| :---- |

**S \-\> P1**

| {   "type": "PRIORITY\_GRANT", "player\_id": "player\_1", "seq\_num": 40, "time\_limit\_ms": 60000 } |
| :---- |

**C \-\> S  (Player 1 passes)**

| {   "type": "PRIORITY\_PASS", "seq\_num": 40 } |
| :---- |

**S \-\> ALL  (advance to Draw)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 41,   "from\_phase": "UPKEEP", "to\_phase": "DRAW",   "active\_player": "player\_2", "turn": 2 } |
| :---- |

## **Step 24 \- Draw Step (Player 2 draws island\_002)**

The server draws one card for Player 2\. Player 2 now has 7 cards in hand. A priority window opens; both players pass and the server advances to Precombat Main.[^11]

**S \-\> P2  (personalized update with new card)**

| {   "type": "GAME\_STATE\_UPDATE", "seq\_num": 42,   "state": {     "turn": 2, "phase": "DRAW", "active\_player": "player\_2",     "life\_totals": { "player\_1": 20, "player\_2": 20 },     "hand": \["counterspell\_001","gray\_merchant\_001","island\_001","swamp\_001","gray\_merchant\_002","swamp\_002","island\_002"\],     "hand\_counts": { "player\_1": 6 },     "library\_counts": { "player\_1": 0, "player\_2": 0 }, "stack": \[\]   } } |
| :---- |

Priority exchange: Player 2 passes, Player 1 passes, empty stack. Server advances.

**S \-\> ALL  (advance to Precombat Main)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 45,   "from\_phase": "DRAW", "to\_phase": "PRECOMBAT\_MAIN",   "active\_player": "player\_2", "turn": 2 } |
| :---- |

## **Step 25 \- Precombat Main Phase: Play a Land**

Player 2 plays swamp\_001. The server places it on the battlefield, sets land\_played to true, and re-issues PRIORITY\_GRANT to Player 2\.

**C \-\> S  (Player 2 plays land)**

| {   "type": "PLAY\_LAND", "seq\_num": 45, "card\_id": "swamp\_001" } |
| :---- |

**S \-\> ALL  (swamp\_001 enters battlefield)**

| {   "type": "GAME\_STATE\_UPDATE", "seq\_num": 46,   "state": {     "turn": 2, "phase": "PRECOMBAT\_MAIN", "active\_player": "player\_2",     "life\_totals": { "player\_1": 20, "player\_2": 20 }, "land\_played": true,     "battlefield": { "player\_1": \[         { "id": "mountain\_003", "tapped": false },         { "id": "goblin\_guide\_001", "tapped": false }       \], "player\_2": \[{ "id": "swamp\_001", "tapped": false }\] },     "hand\_counts": { "player\_1": 6, "player\_2": 6 }, "stack": \[\]   } } |
| :---- |

**S \-\> P2  (re-issue priority after land)**

| {   "type": "PRIORITY\_GRANT", "player\_id": "player\_2", "seq\_num": 46, "time\_limit\_ms": 60000 } |
| :---- |

## **Step 26 \- Precombat Main Phase: Player 1 casts Lightning Bolt**

Player 2 passes priority. Player 1 (NAP) receives priority and casts lightning\_bolt\_001 targeting Player 2, tapping mountain\_003 to pay 1 Red mana. Player 2 holds counterspell\_001 but only has swamp\_001 available — they cannot pay the UU cost. Player 2 passes. Lightning Bolt resolves, dealing 3 damage to Player 2.[^12]

**C \-\> S  (Player 2 passes)**

| {   "type": "PRIORITY\_PASS", "seq\_num": 46 } |
| :---- |

**S \-\> P1  (NAP receives priority)**

| {   "type": "PRIORITY\_GRANT", "player\_id": "player\_1", "seq\_num": 47, "time\_limit\_ms": 60000 } |
| :---- |

**C \-\> S  (Player 1 casts Lightning Bolt targeting Player 2\)**

| {   "type": "CAST\_SPELL", "seq\_num": 47,   "card\_id": "lightning\_bolt\_001", "targets": \["player\_2"\],   "mana\_payment": { "R": 1 } } |
| :---- |

**S \-\> ALL  (Lightning Bolt pushed to stack)**

| {   "type": "STACK\_PUSH", "seq\_num": 48,   "stack\_item\_id": "stk\_02", "item\_type": "SPELL",   "source": "lightning\_bolt\_001", "targets": \["player\_2"\], "controller": "player\_1" } |
| :---- |

**S \-\> P1  (AP retains priority after casting)**

| {   "type": "PRIORITY\_GRANT", "player\_id": "player\_1", "seq\_num": 48, "time\_limit\_ms": 60000 } |
| :---- |

**C \-\> S  (Player 1 passes)**

| {   "type": "PRIORITY\_PASS", "seq\_num": 48 } |
| :---- |

**S \-\> P2**

| {   "type": "PRIORITY\_GRANT", "player\_id": "player\_2", "seq\_num": 49, "time\_limit\_ms": 60000 } |
| :---- |

**C \-\> S  (Player 2 passes — cannot pay UU for Counterspell)**

| {   "type": "PRIORITY\_PASS", "seq\_num": 49 } |
| :---- |

Both players passed consecutively with a non-empty stack. Server resolves the top item.[^13]

**S \-\> ALL  (Lightning Bolt resolves — 3 damage to Player 2\)**

| {   "type": "STACK\_RESOLVE", "seq\_num": 50,   "stack\_item\_id": "stk\_02", "result": "RESOLVED",   "state\_changes": \[{ "type": "DAMAGE", "target": "player\_2", "amount": 3 }\] } |
| :---- |

**S \-\> ALL  (updated life totals)**

| {   "type": "GAME\_STATE\_UPDATE", "seq\_num": 51,   "state": {     "turn": 2, "phase": "PRECOMBAT\_MAIN", "active\_player": "player\_2",     "life\_totals": { "player\_1": 20, "player\_2": 17 }, "land\_played": true,     "battlefield": { "player\_1": \[         { "id": "mountain\_003", "tapped": true },         { "id": "goblin\_guide\_001", "tapped": false }       \], "player\_2": \[{ "id": "swamp\_001", "tapped": false }\] },     "hand\_counts": { "player\_1": 5, "player\_2": 6 }, "stack": \[\]   } } |
| :---- |

Priority re-opens. Both players pass with an empty stack. Server advances to Combat.

**S \-\> ALL  (advance to Combat)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 54,   "from\_phase": "PRECOMBAT\_MAIN", "to\_phase": "BEGIN\_COMBAT",   "active\_player": "player\_2", "turn": 2 } |
| :---- |

## **Step 27 \- Combat Phase (no attackers)**

Player 2 has no creatures on the battlefield and declares no attackers. Both players pass at Begin Combat. The server skips to End of Combat.

**S \-\> ALL  (advance to Declare Attackers)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 57,   "from\_phase": "BEGIN\_COMBAT", "to\_phase": "DECLARE\_ATTACKERS",   "active\_player": "player\_2", "turn": 2 } |
| :---- |

**S \-\> P2**

| {   "type": "PRIORITY\_GRANT", "player\_id": "player\_2", "seq\_num": 57, "time\_limit\_ms": 60000 } |
| :---- |

**C \-\> S  (Player 2 declares no attackers)**

| {   "type": "DECLARE\_ATTACKERS", "seq\_num": 57, "attackers": \[\] } |
| :---- |

**S \-\> ALL  (skip to End of Combat)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 58,   "from\_phase": "DECLARE\_ATTACKERS", "to\_phase": "END\_OF\_COMBAT",   "active\_player": "player\_2", "turn": 2 } |
| :---- |

## **Step 28 \- Postcombat Main Phase (both pass)**

Priority window opens. Player 2 has no further actions. Both pass with an empty stack.

**S \-\> ALL  (advance to Postcombat Main)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 61,   "from\_phase": "END\_OF\_COMBAT", "to\_phase": "POSTCOMBAT\_MAIN",   "active\_player": "player\_2", "turn": 2 } |
| :---- |

**S \-\> ALL  (advance to End Step)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 64,   "from\_phase": "POSTCOMBAT\_MAIN", "to\_phase": "END\_STEP",   "active\_player": "player\_2", "turn": 2 } |
| :---- |

## **Step 29 \- End Step (both pass)**

Final priority window of the turn. Both players pass with an empty stack.

**S \-\> ALL  (advance to Cleanup)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 67,   "from\_phase": "END\_STEP", "to\_phase": "CLEANUP",   "active\_player": "player\_2", "turn": 2 } |
| :---- |

## **Step 30 \- Cleanup Step**

Player 2 has 6 cards in hand (under the 7-card limit — drew 1, played swamp\_001), so no discard is needed. The server clears damage markers from all creatures. mountain\_003 untaps at the start of Player 1's next turn, not here. The server increments the turn counter, switches the Active Player to Player 1, and begins Turn 3's Untap Step.[^14]

**S \-\> ALL  (damage cleared)**

| {   "type": "GAME\_STATE\_UPDATE", "seq\_num": 68,   "state": {     "turn": 2, "phase": "CLEANUP", "active\_player": "player\_2",     "life\_totals": { "player\_1": 20, "player\_2": 17 },     "battlefield": { "player\_1": \[         { "id": "mountain\_003", "tapped": true },         { "id": "goblin\_guide\_001", "tapped": false }       \], "player\_2": \[{ "id": "swamp\_001", "tapped": false }\] },     "hand\_counts": { "player\_1": 5, "player\_2": 6 }, "stack": \[\]   } } |
| :---- |

**S \-\> ALL  (Turn 3 begins — Player 1 is Active Player again)**

| {   "type": "PHASE\_TRANSITION", "seq\_num": 69,   "from\_phase": "CLEANUP", "to\_phase": "UNTAP",   "active\_player": "player\_1", "turn": 3 } |
| :---- |

# **6\.  GAME\_OVER State**

NOTE: Several turns have elapsed between Turn 2 and the exchange shown below. Over the course of those turns, Goblin Guide attacked repeatedly, and Player 1 used additional burn spells to reduce Player 2's life total. Player 2 managed to deploy Gray Merchant of Asphodel, draining Player 1 for some life, but was never able to stabilize the board. By Turn 7, the game state entering Player 1's Precombat Main Phase is as follows:

| Life totals:  Player 1 \= 14,  Player 2 \= 3 Battlefield:   Player 1: mountain\_001, mountain\_002, mountain\_003 (all untapped),             goblin\_guide\_001 (untapped)   Player 2: swamp\_001, island\_001 (both untapped) Player 1 hand: lightning\_bolt\_003, shock\_002 Player 2 hand: counterspell\_001 seq\_num at start of this exchange: 118 |
| :---- |

## **Step 31 \- Player 1 casts Lightning Bolt targeting Player 2 (lethal)**

Player 1 casts lightning\_bolt\_003, targeting Player 2 who is at 3 life. The Bolt deals 3 damage — exactly enough to reduce Player 2's life total to 0\.

**S \-\> P1  (priority granted in Precombat Main)**

| {   "type": "PRIORITY\_GRANT", "player\_id": "player\_1", "seq\_num": 118, "time\_limit\_ms": 60000 } |
| :---- |

**C \-\> S  (Player 1 casts Lightning Bolt)**

| {   "type": "CAST\_SPELL", "seq\_num": 118,   "card\_id": "lightning\_bolt\_003", "targets": \["player\_2"\],   "mana\_payment": { "R": 1 } } |
| :---- |

**S \-\> ALL  (Lightning Bolt pushed to stack)**

| {   "type": "STACK\_PUSH", "seq\_num": 119,   "stack\_item\_id": "stk\_09", "item\_type": "SPELL",   "source": "lightning\_bolt\_003", "targets": \["player\_2"\], "controller": "player\_1" } |
| :---- |

**S \-\> P1  (AP retains priority)**

| {   "type": "PRIORITY\_GRANT", "player\_id": "player\_1", "seq\_num": 119, "time\_limit\_ms": 60000 } |
| :---- |

**C \-\> S  (Player 1 passes)**

| {   "type": "PRIORITY\_PASS", "seq\_num": 119 } |
| :---- |

**S \-\> P2**

| {   "type": "PRIORITY\_GRANT", "player\_id": "player\_2", "seq\_num": 120, "time\_limit\_ms": 60000 } |
| :---- |

**C \-\> S  (Player 2 passes — counterspell\_001 requires UU, only BU available)**

| {   "type": "PRIORITY\_PASS", "seq\_num": 120 } |
| :---- |

Both players passed consecutively. Server resolves the top item.

**S \-\> ALL  (Lightning Bolt resolves — 3 damage to Player 2\)**

| {   "type": "STACK\_RESOLVE", "seq\_num": 121,   "stack\_item\_id": "stk\_09", "result": "RESOLVED",   "state\_changes": \[{ "type": "DAMAGE", "target": "player\_2", "amount": 3 }\] } |
| :---- |

## **Step 32 \- Server detects win condition and broadcasts GAME\_OVER**

Player 2's life total has reached 0\. The server immediately detects the LIFE\_ZERO win condition, skips any further priority windows, and broadcasts GAME\_OVER to all connected players. Player 1 is declared the winner.[^15]

**S \-\> ALL**

| {   "type":      "GAME\_OVER",   "seq\_num":   122,   "winner\_id": "player\_1",   "loser\_id":  "player\_2",   "reason":    "LIFE\_ZERO" } |
| :---- |

The reason field identifies how the game ended.[^16]

winner\_id is always set to the non-offending or surviving player.[^17]

## **Step 33 \- Server transitions back to LOBBY**

Immediately after broadcasting GAME\_OVER, the server transitions back to LOBBY state. The existing TCP connections are retained. Both players must send a fresh PLAYER\_READY PDU to begin a new game. The server does not broadcast a PHASE\_TRANSITION for this — the GAME\_OVER PDU itself signals the return to LOBBY.[^18]

[^1]: A player who has never mulliganed keeps with an empty cards\_to\_bottom array, since N \= 0\.

[^2]: Only the mulliganing player receives a new GAME\_STATE\_UPDATE after a redraw. The other player receives no PDU until both have kept and the server broadcasts PHASE\_TRANSITION.

[^3]: When keep is false, cards\_to\_bottom MUST be empty. When keep is true, cards\_to\_bottom MUST contain exactly N card IDs where N equals the number of mulligans taken. The server rejects a mismatch with ERROR code ILLEGAL\_ACTION.

[^4]: Players decide independently — each player's MULLIGAN\_CHOICE is processed separately. Player 1's keep does not block or affect Player 2's mulligan decision.

[^5]: seq\_num on PHASE\_TRANSITION continues the server counter from the last GAME\_STATE\_UPDATE sent to either player — here seq\_num 5 follows seq\_num 4, the redraw sent to Player 2\.

[^6]: The Untap Step has no priority window. The server performs all untap actions automatically and advances to Upkeep without waiting for any client PDU.

[^7]: Every priority window follows the same pattern: PRIORITY\_GRANT to AP, PRIORITY\_PASS from AP, PRIORITY\_GRANT to NAP, PRIORITY\_PASS from NAP — then PHASE\_TRANSITION if the stack is empty. Only the Precombat Main spell-casting window is shown in full detail; other windows are summarised for brevity.

[^8]: Per the RFC, on the very first turn the first player does NOT draw a card during the Draw Step. This example shows a representative turn with a draw for clarity; a strict Turn 1 implementation would skip the card draw and open the priority window on an unchanged hand.

[^9]: PLAY\_LAND bypasses the stack entirely. The server deducts the land from the hand, places it on the battlefield, sets land\_played to true, and re-issues PRIORITY\_GRANT to the Active Player.

[^10]: A creature has summoning sickness the turn it enters the battlefield. It MUST NOT be declared as an attacker and MUST NOT activate tap abilities until the controller's next Untap Step.

[^11]: Player 2's library reaches 0 after this draw. An 8-card deck minus 7 drawn at setup minus 1 bottomed during mulligan leaves 0 cards. Any draw attempt on Turn 3 or later triggers the DECK\_EMPTY win condition.

[^12]: Player 1 receiving priority during Player 2's Precombat Main Phase is legal. When the Active Player passes priority, the Non-Active Player receives it and may cast instants or activate abilities at instant speed.

[^13]: Counterspell costs UU. Player 2's only mana source is swamp\_001, which produces Black mana. Had Player 2 attempted the cast, the server would have returned ERROR code INSUFFICIENT\_MANA.

[^14]: mountain\_003 remains tapped through Cleanup — lands are not untapped during the owner's Cleanup Step. They untap at the start of the owner's next Untap Step.

[^15]: LIFE\_ZERO is detected immediately after STACK\_RESOLVE applies damage — no further priority windows are granted before GAME\_OVER is broadcast.

[^16]: Valid reason values: LIFE\_ZERO (life total reaches 0), DECK\_EMPTY (draw from empty library), CONCEDE (player sends CONCEDE PDU), DISCONNECT (connection lost, reconnect timer expired).

[^17]: winner\_id is the non-offending or surviving player in all cases.

[^18]: TCP connections are preserved across GAME\_OVER. Both players can start a new game immediately by sending PLAYER\_READY on the same connection — no reconnection required.