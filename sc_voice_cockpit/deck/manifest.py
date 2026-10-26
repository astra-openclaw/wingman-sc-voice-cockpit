"""Attach sync without silently accepting previously drifted or edited commands."""
import copy

def merge_manifest(previous, entries, commands, updates, core):
    result=copy.deepcopy(previous)
    rows={row["name"]:row for row in result.get("commands",[])}
    before={command["name"]:command for command in commands}
    changed={row["name"] for row in updates}
    for entry in entries:
        name=entry["name"]
        if name not in rows:
            rows[name]=copy.deepcopy(entry)
        elif (entry.get("owned_emote") and name in before and
              rows[name].get("fingerprint")==core.command_fingerprint(before[name]) and
              not rows[name].get("expectedActions")):
            # The old curated matcher excluded all player_emotes. Only a verified
            # exact generated command can fill that empty audit; never rebaseline drift.
            rows[name]=copy.deepcopy(entry)
        elif (name in changed and name in before and
              rows[name].get("fingerprint")==core.command_fingerprint(before[name])):
            # Only the recognized silent tail was appended. Keep the audited
            # intended actions and activation rules; change the fingerprint only.
            rows[name]["fingerprint"]=entry["fingerprint"]
        # An existing edited command stays EDITED; a moved key stays DRIFT.
        # The existing self-heal/fresh-start paths own acceptance of those changes.
    result["commands"]=list(rows.values())
    return result
