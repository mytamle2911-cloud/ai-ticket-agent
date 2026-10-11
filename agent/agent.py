from agent.llm_agent import analyze_ticket


if __name__ == "__main__":
    incident_number = input("Enter incident number: ").strip()
    analyze_ticket(incident_number)