# AgentSafe — spécification du MVP hackathon

## Produit

AgentSafe est une couche de contrôle et d'audit installée dans l'environnement du client. Elle protège les opérations que l'entreprise choisit de couvrir : délégations, accès aux outils, budgets, transferts de données et procédures obligatoires. L'agent propose une action; le contrôleur vérifie le mandat avant d'appeler l'outil. Une action qui ne passe pas par le contrôleur n'est pas protégée.

La console montre deux relations : **exécution** (agents, outils, transferts) et **autorisation** (qui a délégué quels droits). L'entreprise peut suspendre une mission ou révoquer un mandat; cela bloque les prochaines actions, sans annuler celles déjà exécutées.

## Garanties visées par le prototype

- Une opération couverte est liée à une mission et à un mandat signé, limité par outil, ressource, budget, durée et délégation.
- Une délégation ne peut pas augmenter le budget, étendre le périmètre, prolonger l'expiration, supprimer une restriction de données ou réactiver la délégation si le parent l'interdit.
- Les limites de budget cumulées sont vérifiées et mises à jour atomiquement; un même identifiant de requête ne peut pas être rejoué.
- Les sorties dérivées de données confidentielles restent confidentielles par défaut. Le contrôleur vérifie les destinations déclarées avant le transfert.
- Une action nécessitant une approbation ou une preuve est bloquée si le justificatif correspondant à cette opération manque, est expiré ou a déjà été consommé.
- La décision est durablement enregistrée avant l'exécution. L'échec d'écriture bloque l'action. Le résultat est enregistré séparément et peut être `unknown` si l'outil a répondu de manière ambiguë.
- Les reçus signés attestent ce que le contrôleur a vérifié ou observé; ils ne prouvent ni le raisonnement interne ni la vérité d'une sortie d'outil.

## Démonstration ciblée

Le scénario MVP utilise deux agents, deux outils, des données classifiées et une délégation :

1. A reçoit un mandat de mission avec budget, outils, durée et restrictions de données.
2. A délègue à B un sous-ensemble limité de ces droits; une preuve ZK Groth16 démontre que le sous-mandat respecte son parent sans publier ses champs privés au vérificateur.
3. Une opération autorisée passe; une sortie confidentielle vers une destination externe est bloquée.
4. Le contrôleur refuse un dépassement du budget cumulé, une procédure sans justificatif, une requête rejouée et une opération après révocation.
5. Un vérificateur indépendant détecte une altération du journal chaîné.
6. Le mode simulation teste des demandes permises et interdites sans appeler de connecteur réel.

## Architecture cible

| Composant | Choix du prototype | Responsabilité |
| --- | --- | --- |
| Contrôleur et API | Python, FastAPI, Pydantic | Vérifier puis exécuter les opérations protégées |
| État local | SQLite | Mandats, budgets, justificatifs, révocations et journal |
| Signatures | Ed25519, JWS/JWT | Authentifier les mandats, demandes et reçus |
| Intégrité | SHA-256 | Empreinte et chaînage des événements |
| Délégation privée | Circom 2, circomlib/Poseidon, Groth16, snarkjs | Prouver la relation parent-enfant lors d'une délégation, pas à chaque appel d'outil |
| Console | HTML/CSS/JavaScript servi par FastAPI | Inventaire des mandats, groupes, journal SIEM JSONL, alertes et intervention |

Les clés privées restent dans les composants locaux de signature, jamais dans les prompts. Les détails des documents et des prompts ne sont pas copiés dans le journal. Les engagements de données prévisibles doivent inclure un aléa secret.

## Limites et sécurité

Le journal est chaîné et signé, mais un administrateur qui contrôle le stockage peut supprimer une fin non encore attestée. Des points de contrôle signés conservés séparément réduisent ce risque; le prototype ne qualifie pas le journal d'inviolable. Une signature authentifie un label de donnée mais ne prouve pas que sa classification initiale était correcte. Le setup Groth16 de démonstration n'est pas une cérémonie auditée et n'est pas adapté à la production.

## État actuel du dépôt

Le contrôleur local `controller/` est maintenant exécutable avec FastAPI, SQLite, Ed25519, un tableau de bord local et deux agents de démonstration structurés comme nœuds dans un LangGraph `StateGraph`. Le graphe sert de harnais de test déterministe; il ne fait pas d'appel LLM. Les mandats et les requêtes des agents sont signés; les demandes de délégation sont signées par l'agent parent; les périmètres, budgets réservés et cumulés, approbations exactes à usage unique, expiration et révocation sont contrôlés. Une décision est écrite avant l'exécution des deux connecteurs locaux de démonstration. Les reçus sont signés. Le vérificateur indépendant peut contrôler un export du journal SHA-256 chaîné et signé. Les paramètres privés des actions restent hors du journal; les ressources et destinations y sont représentées par des engagements HMAC calculés avec la clé locale du contrôleur.

Le scénario vérifié montre une action interne permise, une sortie confidentielle externe bloquée, un achat bloqué sans approbation, un achat local permis après clic explicite, un dépassement du budget délégué bloqué et une action post-révocation bloquée. Les délégations compilent le circuit Circom, produisent une preuve Groth16 et la vérifient dans un processus snarkjs séparé avant d'émettre le mandat enfant. Le contenu des données n'est pas recopié dans le journal; son empreinte fait partie de l'autorisation signée.

La console comporte quatre vues SOC : (1) identité, mandat, périmètre, restrictions, limites, expiration, délégation et kill switch; (2) policies de groupe avec budget partagé; (3) flux d'audit signé et export JSONL; (4) alertes ASI02, ASI03 et crypto avec résolution et révocation manuelle/automatique. Trois refus du même mandat en 60 secondes déclenchent la révocation de son arbre de délégation. Les événements sont écrits dans `controller/.data/audit.log` pour collecte locale.

Les limites restent visibles : aucun outil externe n'est connecté; l'approbateur est une interaction de démonstration non authentifiée; les labels viennent de l'appelant et ne sont pas attestés par un connecteur source; la cérémonie de setup Groth16 est locale à une partie et non auditée; aucun point de contrôle externe ne protège la fin du journal; les clés et rôles d'entreprise ne sont pas provisionnés. Les membres de groupe restent un roster et ne reçoivent pas encore de justificatifs cryptographiques individuels. L'application publiée `https://agentsafe-nu.vercel.app` sert maintenant le contrôleur FastAPI, son tableau de bord et la fonction Node.js séparée qui génère/vérifie les preuves. Elle persiste dans Neon PostgreSQL; les agents et actions de démonstration restent synthétiques. Le contrôleur n'ancre pas encore le journal sur Avalanche Fuji.
