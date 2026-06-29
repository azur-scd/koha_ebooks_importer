# koha_ebooks_importer

## Présentation

`koha_ebooks_importer` est une application Python de bureau destinée à faciliter le traitement, l’enrichissement et l’export de notices d’ebooks en vue de leur intégration dans **Koha**.

L’outil cible un cas d’usage métier précis : partir de notices **UNIMARC ISO2709** fournies pour des ebooks BiblioOnDemand, permettre leur contrôle, leur sélection, leur enrichissement à partir de sources externes, puis produire un export exploitable dans le SIGB.

L’application fournit une interface graphique simple pour accompagner les opérations de catalogage et de préparation de données, sans nécessiter de manipulation manuelle directe des fichiers MARC.

## Objectifs fonctionnels

L’application permet de :

- importer un fichier de notices **UNIMARC ISO2709** encodé en UTF-8 ;
- afficher les notices chargées dans une interface de contrôle ;
- sélectionner tout ou partie des notices à traiter ;
- dédoublonner les notices importées ;
- préparer les notices pour leur intégration dans **Koha** ;
- enrichir les notices via une collecte **OAI-PMH** en Dublin Core ;
- croiser les notices préparées avec les données collectées ;
- enrichir les notices avec des informations provenant du **Sudoc** ;
- générer des rapports de traitement ;
- exporter le résultat final au format **MARCXML**.

## Fonctionnement général

Le traitement suit une chaîne logique en plusieurs étapes :

1. **Import des notices source**  
   L’utilisateur charge un fichier UNIMARC ISO2709 depuis l’interface.

2. **Contrôle et dédoublonnage**  
   Les notices sont analysées et dédoublonnées, notamment à partir de l’identifiant de notice et de l’EAN.

3. **Sélection des notices**  
   L’utilisateur choisit les notices à traiter.

4. **Préparation pour Koha**  
   L’application applique des transformations métier sur les notices sélectionnées pour les importer dans Koha : nettoyage et harmonisation des données, ajout de champs spécifiques à Koha (en particulier 099 et 995)
   L'utilisateur peut poursuivre le traitement ou exporter les notices (étape 9). 

5. **Récupération des notices en Dublin Core via OAI-PMH**  
   Récupération des notices en Dublin Core depuis l'entrepôt OAI-PMH du fournisseur

6. **Enrichissement des notices à partir des notices Dublin Core**  
   Croisement des notices en fonction de l'EAN (en supprimant le suffixe souvent présent dans les notices DC) et enrichissement. L'intérêt principal est de récupérer les liens vers les vignettes, rarement présent en UNIMARC, et l'ISBN, souvent fictif dans les notices UNIMARC.
   L'utilisateur peut poursuivre le traitement ou exporter les notices (étape 9).
   
7. **Enrichissement des notices à partir des notices SUDOC**  
   Récupération des données SUDOC à partir de l'ISBN et enrichissement des notices, lorsqu'une notice SUDOC est trouvée. L'application privilégie les notices de documents physiques, et à défaut utilise les notices d'ebook. L'intérêt principal est de récupérer le résumé Sudoc, la table des matières, l'indexation sujet, les formes normalisées des auteurs.
   
8. **Recherche des notices Koha**  
   Vérifie si les notices sont déjà dans Koha (dans le cas d'une mise à jour) : interroge Koha par SRU en se basant sur l'EAN
   
9. **Export en MARCXML**  
   Export des notices en MARCXML, prêtes à être importées dans Koha

   
   

   
