from flask import request, jsonify
from macpepdb.proteomics.amino_acid import AminoAcid
from macpepdb.proteomics.mass.convert import to_float as mass_to_float
from macpepdb.models.protein import Protein
from macpepdb.models.peptide import Peptide
from macpepdb.models.protein_peptide_association import ProteinPeptideAssociation
from macpepdb.models.taxonomy import Taxonomy

from macpepdb_web_backend import app, get_database_connection
from macpepdb_web_backend.models.convert import peptide_to_dict, protein_to_dict
from macpepdb_web_backend.controllers.application_controller import ApplicationController
from macpepdb_web_backend.controllers.api.api_digestion_controller import ApiDigestionController

class ApiProteinsController(ApplicationController):
    @staticmethod
    @app.route("/api/proteins/<string:accession>", endpoint="api_protein_path")
    def show(accession: str):
        accession = accession.upper()

        database_connection = get_database_connection()

        with database_connection.cursor() as database_cursor:
            protein = Protein.select(database_cursor, ("accession = %s", [accession]))

            if protein:
                response_body = protein_to_dict(protein)
                database_cursor.execute(f"SELECT name FROM {Taxonomy.TABLE_NAME} WHERE id = %s;", (protein.taxonomy_id,))
                taxonomy_name_row = database_cursor.fetchone()
                response_body["taxonomy_name"] = taxonomy_name_row[0] if taxonomy_name_row else None

                return jsonify(response_body)
            else:               
                return jsonify({
                    "errors": {
                        "accession": ["not found"]
                    }
                }), 404

    @staticmethod
    @app.route("/api/proteins/<string:accession>/peptides", endpoint="api_protein_peptides_path")
    def peptides(accession: str):

        database_connection = get_database_connection()
        with database_connection.cursor() as database_cursor:
                peptide_query = (
                    f"SELECT mass, sequence, number_of_missed_cleavages, is_swiss_prot, is_trembl, taxonomy_ids, unique_taxonomy_ids, proteome_ids FROM {Peptide.TABLE_NAME} "
                    f"WHERE (mass, sequence) IN (SELECT peptide_mass, peptide_sequence FROM {ProteinPeptideAssociation.TABLE_NAME} WHERE protein_accession = %s) ORDER BY mass ASC;"
                )
                
                database_cursor.execute(peptide_query, (accession,))

                return jsonify({
                    "peptides": [{
                        "mass": mass_to_float(row[0]),
                        "sequence": row[1],
                        "number_of_missed_cleavages": row[2],
                        "is_swiss_prot": row[3],
                        "is_trembl": row[4],
                        "taxonomy_ids": row[5],
                        "unique_taxonomy_ids": row[6],
                        "proteome_ids": row[7]
                    } for row in database_cursor.fetchall()]
                })


    @staticmethod
    @app.route("/api/proteins/digest", endpoint="api_protein_digest_path", methods=["POST"])
    def digest():
        """
        Digests the seqeunce of the given protein.
        """
        data = request.get_json()
        errors = ApiDigestionController.check_digestion_parameters(data)

        if not "accession" in data:
            errors["accession"].append("cannot be empty")

        peptides = []
        if len(errors) == 0:
            database_connection = get_database_connection()
            with database_connection.cursor() as database_cursor:
                protein = Protein.select(database_cursor, ("accession = %s", [data["accession"]]))
                if protein:
                    peptides = list(filter(
                        lambda peptide: peptide.number_of_missed_cleavages <= data["maximum_number_of_missed_cleavages"] and data["minimum_peptide_length"] <= peptide.length <= data["maximum_peptide_length"],
                        protein.peptides(database_cursor)
                    ))
                    peptides.sort(key = lambda peptide: peptide.mass)
                else:
                    errors["accession"].append("not found")

        if len(errors) == 0:
            return jsonify({
                "peptides": [peptide_to_dict(peptide) for peptide in peptides],
                "count": len(peptides)
            })
        else:
            return jsonify({
                "errors": errors
            }), 422

    @staticmethod
    @app.route("/api/proteins/amino-acids", endpoint="api_protein_amino_acids_path")
    def amino_acids():
        return jsonify({
            "amino_acids": [{
                "one_letter_code": amino_acid.one_letter_code,
                "name": amino_acid.name
            } for amino_acid in AminoAcid.all()]
        })